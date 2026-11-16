"""Offline clock, scope and persistence regressions for evidence reuse."""
from datetime import timedelta
import unittest

from app.freshness import FreshnessContext, FreshnessPolicy, QuoteValidity, assess_evidence
from app.contracts_v2 import RunReference
import test_corpus as fixtures
from test_corpus import START, citation, extract, unsupported


class FreshnessTests(unittest.TestCase):
    setUp = fixtures.CorpusTests.setUp
    capture = fixtures.CorpusTests.capture
    fields = fixtures.CorpusTests.fields

    def context(self, **changes):
        return FreshnessContext(now=START, product_id='book', variant_id='book-16').model_copy(update=changes)

    def receipt(self, field='variant.specs.ram_gb'):
        return citation(self.capture(), field, '"ram":16')

    def test_specification_and_policy_boundary_and_earlier_expiry(self):
        for field, days in [('variant.specs.ram_gb', 30), ('policy.returns', 7)]:
            receipt = self.receipt(field)
            if field.startswith('policy.'):
                receipt = receipt.model_copy(update={'scope':'source', 'product_id':None, 'variant_id':None})
            self.assertTrue(assess_evidence(receipt, self.context(now=START+timedelta(days=days, microseconds=-1))).reusable)
            self.assertEqual(assess_evidence(receipt, self.context(now=START+timedelta(days=days))).state, 'historical')
            self.assertEqual(assess_evidence(receipt, self.context(), expires_at=START).state, 'historical')
        self.assertEqual(assess_evidence(self.receipt(), self.context(now=START+timedelta(days=2)),
            policy=FreshnessPolicy(specifications=timedelta(days=1))).state, 'historical')

    def test_changed_variant_conflict_future_and_failed_refresh(self):
        receipt = self.receipt()
        self.assertEqual(assess_evidence(receipt, self.context(variant_id='book-32')).state,'unknown')
        self.assertEqual(assess_evidence(receipt, self.context(), fact_state='conflict').state,'conflict')
        self.assertEqual(assess_evidence(receipt, self.context(now=START-timedelta(seconds=1))).state,'unknown')
        for status in ('failed','denied','absent'):
            self.assertEqual(assess_evidence(receipt, self.context(), check_status=status).state,'historical')

    def test_current_run_price_stock_and_branch_scope(self):
        run = RunReference(run_id='r', run_revision=1, request_id='q', request_revision=1)
        context = self.context(run=run, run_started_at=START)
        for field in ('price','stock'):
            cached = self.receipt(field)
            self.assertEqual(assess_evidence(cached, context).state,'historical')
            fresh = cached.model_copy(update={'origin':'runtime','run':run})
            self.assertTrue(assess_evidence(fresh, context).reusable)
            self.assertEqual(assess_evidence(fresh, context.model_copy(update={'run_started_at':START+timedelta(seconds=1)})).state,'historical')
            self.assertEqual(assess_evidence(fresh, context.model_copy(update={'run':run.model_copy(update={'request_revision':2})})).state,'historical')
        self.assertEqual(assess_evidence(fresh, context.model_copy(update={'branch_id':'hamra'})).state,'unknown')

    def test_quote_expiry_and_package_route_changes(self):
        receipt = self.receipt('charges.shipping').model_copy(update={'scope':'route','route_id':'route-a'})
        context = self.context(route_id='route-a',package_fingerprint='package-a')
        quote = QuoteValidity(evidence_id=receipt.evidence_id,route_id='route-a',
            package_fingerprint='package-a',valid_until=START+timedelta(hours=1))
        self.assertTrue(assess_evidence(receipt,context,quote=quote).reusable)
        for changes in ({'package_fingerprint':'package-b'},{'route_id':'route-b'}):
            self.assertEqual(assess_evidence(receipt,context.model_copy(update=changes),quote=quote).state,'unknown')
        self.assertEqual(assess_evidence(receipt,context.model_copy(update={'now':quote.valid_until}),quote=quote).state,'historical')
        self.assertEqual(assess_evidence(receipt,context).state,'unknown')

    def test_304_fingerprint_and_index_changes_do_not_refresh_fact_age(self):
        first = self.corpus.ingest(self.capture(),extract).document_id
        prior = self.corpus.state(self.key)
        self.corpus.record_not_modified(self.key,prior,START+timedelta(days=31))
        result = dict((o.evidence[0].field_path, f) for o,f in self.corpus.assessed_observations(
            self.key,self.context(now=START+timedelta(days=31))))
        self.assertEqual(result['variant.specs.ram_gb'].state,'historical')
        self.assertEqual(self.fields(first)['variant.specs.ram_gb'].evidence[0].observed_at,START)
        self.corpus.ingest(self.capture(32*86400),unsupported)
        self.assertEqual(self.corpus.assessed_observations(self.key,self.context(now=START+timedelta(days=32)))[1][1].state,'historical')

    def test_explicit_revalidation_refreshes_only_supported_field_and_keeps_history(self):
        first = self.corpus.ingest(self.capture(),extract).document_id
        later = self.corpus.ingest(self.capture(86400),extract,revalidate_fields=('variant.specs.ram_gb',)).document_id
        self.assertEqual(self.fields(later)['variant.specs.ram_gb'].evidence[0].observed_at,START+timedelta(days=1))
        self.assertEqual(self.fields(later)['price'].evidence[0].observed_at,START)
        self.assertEqual(self.fields(first)['variant.specs.ram_gb'].evidence[0].observed_at,START)
        with self.assertRaisesRegex(ValueError,'every named field'):
            self.corpus.ingest(self.capture(2*86400),extract,revalidate_fields=('stock',))

    def test_supported_removal_survives_failed_checks_and_retains_history(self):
        first = self.corpus.ingest(self.capture(),extract).document_id
        capture = self.capture(60).model_copy(update={'body':b'Product discontinued', 'relevant_content':b'Product discontinued'})
        receipt = citation(capture,'resource.availability','Product discontinued')
        self.corpus.record_retirement(capture,receipt,status='discontinued')
        self.corpus.record_failed_check(self.key,START+timedelta(seconds=120),status='denied')
        self.assertTrue(all(f.state=='retired' for o,f in self.corpus.assessed_observations(self.key,self.context(now=START+timedelta(seconds=120)))))
        self.assertEqual(len(self.corpus.observations(first)),2)
        with self.assertRaisesRegex(ValueError,'exact resource'):
            self.corpus.record_retirement(capture,receipt.model_copy(update={'variant_id':'wrong'}),status='removed')
        self.corpus.ingest(self.capture(180,'110'),extract)
        results = {o.evidence[0].field_path:f for o,f in self.corpus.assessed_observations(
            self.key,self.context(now=START+timedelta(seconds=181)))}
        self.assertEqual(results['variant.specs.ram_gb'].state,'fresh')

    def test_failed_access_and_ambiguous_absence_remain_historical(self):
        self.corpus.ingest(self.capture(),extract)
        for i,status in enumerate(('failed','denied','absent')):
            self.corpus.record_failed_check(self.key,START+timedelta(seconds=i+2),status=status)
            self.assertEqual(self.corpus.state(self.key).check_status,status)
            self.assertTrue(all(f.state=='historical' for o,f in self.corpus.assessed_observations(self.key,self.context(now=START+timedelta(seconds=10)))))

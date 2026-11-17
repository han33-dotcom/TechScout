from decimal import localcontext
import unittest
from pydantic import ValidationError
from app.identity import compare_offers, variant_identity_key, group_variants
from app.contracts_v2 import Offer
from tests.identity_v2 import offer


class MonitorAudioIdentityTests(unittest.TestCase):
    def test_equivalent_source_models_group_without_phone_fields(self):
        for category,model in [('monitor','view 27q v2'),('audio','sound pro v2')]:
            with self.subTest(category=category):
                a=offer(category,'left')
                b=offer(category,'right',{'model':model,'manufacturer':'EXAMPLE','color':'noir'})
                result=compare_offers(a,b)
                self.assertEqual(result.hardware.outcome,'confirmed_equivalent')
                self.assertEqual(variant_identity_key(a),variant_identity_key(b))
                self.assertEqual(len(group_variants([a,b])),1)
                self.assertTrue(result.hardware.evidence_ids)
                self.assertNotIn('activation',result.unresolved_terms)
                self.assertNotIn('sim',result.unresolved_identity_fields)

    def test_monitor_configuration_differences_are_not_collapsed(self):
        for path,value in [('model','View 27Q v2S'),('specs.diagonal_inches','27.1'),
                           ('specs.width_px',3840),('specs.height_px',2160),
                           ('specs.refresh_hz',144),('specs.panel','OLED')]:
            with self.subTest(path=path):
                a,b=offer('monitor','left'),offer('monitor','right',{path:value})
                self.assertEqual(compare_offers(a,b).hardware.outcome,'different_configuration')
                self.assertNotEqual(variant_identity_key(a),variant_identity_key(b))

    def test_audio_versions_form_connectivity_and_anc_are_identity(self):
        for path,value in [('model','Sound Pro v3'),('model','Sound Pro v2 SE'),
                           ('specs.form_factor','earbuds'),('specs.connectivity','wired'),
                           ('specs.active_noise_cancellation',False)]:
            with self.subTest(path=path):
                a,b=offer('audio','left'),offer('audio','right',{path:value})
                self.assertEqual(compare_offers(a,b).hardware.outcome,'different_configuration')
                self.assertEqual(len(group_variants([a,b])),2)

    def test_unknown_absent_and_conflicting_fields_stay_unresolved(self):
        cases=[('monitor','specs.panel',None),('audio','specs.active_noise_cancellation',None),
               ('monitor','specs.refresh_hz',{'state':'unknown','reason':'Not published'}),
               ('audio','specs.connectivity',{'state':'conflict','alternatives':['wired','wireless'],'reason':'Sources disagree'})]
        for category,path,value in cases:
            with self.subTest(path=path):
                a,b=offer(category,'left'),offer(category,'right',{path:value})
                self.assertEqual(compare_offers(a,b).hardware.outcome,'unresolved')
                self.assertIsNone(variant_identity_key(b))

    def test_generic_and_ambiguous_names_do_not_prove_equivalence(self):
        for category,name in [('monitor','Example 27 inch gaming monitor'),('monitor','View 27Q or View 32Q'),
                              ('audio','Wireless headphones'),('audio','Sound Pro series')]:
            a,b=offer(category,'left',{'model':name}),offer(category,'right',{'model':name})
            self.assertEqual(compare_offers(a,b).hardware.outcome,'unresolved')
            self.assertIsNone(variant_identity_key(a))

    def test_exact_model_receipts_are_required_and_terms_remain_separate(self):
        for category in ('monitor','audio'):
            a,b=offer(category,'left'),offer(category,'right',{'condition':'used','warranty.months':6})
            result=compare_offers(a,b)
            self.assertEqual(result.hardware.outcome,'confirmed_equivalent')
            self.assertEqual(set(result.differing_terms),{'condition','warranty.months'})
            raw=b.model_dump(mode='json')
            raw['evidence']=[e for e in raw['evidence'] if e['field_path']!='variant.product.model']
            b=Offer.model_validate(raw)
            self.assertEqual(compare_offers(a,b).hardware.outcome,'unresolved')

    def test_invalid_types_and_unsupported_categories_are_rejected(self):
        for category,path,value in [('monitor','specs.refresh_hz',True),('monitor','specs.diagonal_inches',27.2),
                                   ('monitor','specs.width_px',0),('audio','specs.form_factor','speaker'),
                                   ('audio','specs.connectivity','wifi'),('audio','specs.active_noise_cancellation','yes')]:
            with self.subTest(path=path),self.assertRaises(ValidationError):
                offer(category,changes={path:value})

    def test_exact_decimal_size_survives_low_precision_and_suffixes_survive_normalization(self):
        a=offer('monitor','left',{'specs.diagonal_inches':'27.01'})
        b=offer('monitor','right',{'specs.diagonal_inches':'27.02'})
        with localcontext() as context:
            context.prec=1
            self.assertNotEqual(variant_identity_key(a),variant_identity_key(b))
        a=offer('audio','left',{'model':'SX-1000M5'})
        b=offer('audio','right',{'model':'sx – 1000m5'})
        self.assertEqual(compare_offers(a,b).hardware.outcome,'confirmed_equivalent')
        b=offer('audio','right',{'model':'SX-1000M50'})
        self.assertEqual(compare_offers(a,b).hardware.outcome,'different_configuration')

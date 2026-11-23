"""Offline checks that keep the frozen held-out evaluation protocol (#78) intact and consistent."""
from collections import Counter
import importlib.util
import json
from pathlib import Path
import random
import re
import shutil
import tempfile
import unittest

from app import contracts_v2 as v2
from app.retrieval import ROUTED_MODEL as EMBEDDING_MODEL

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / 'eval'
DOC = ROOT / 'docs' / 'evaluation-protocol.md'

# Changing any frozen file needs `python eval/freeze.py --bump X.Y.Z "reason"` and these two values.
FROZEN_VERSION = '1.1.0'
FROZEN_COMBINED_SHA256 = '279a40a9e74ea2f735358bf42c8e7f481e4005be8d0030d2a46d2b715534fda9'
HOW_TO_CHANGE = ('The evaluation protocol is frozen (#78). For a deliberate change before the pre-run freeze, run '
                 'python eval/freeze.py --bump X.Y.Z "reason" and update FROZEN_VERSION and FROZEN_COMBINED_SHA256 in '
                 'tests/test_eval_protocol.py; see docs/evaluation-protocol.md section 1.')

ARABIC = re.compile(r'[؀-ۿ]')
ARABIZI = re.compile(r'[a-z][237]|[237][a-z]')
ARABIZI_WORDS = {'baddi', 'bade', 'ma3', '3a', 'mn', 'lezem', 'a3la', 'jdide', 'shi'}
# Places a held-out case must never be copied into: the app, its configuration and seed data.
APP_INPUTS = ('app', 'config', 'examples', 'frontend/src', 'migrations', 'tests/fixtures')
TEXT_SUFFIXES = {'.py', '.json', '.jsonl', '.ts', '.tsx', '.js', '.md', '.txt', '.csv', '.html', '.sql', '.yaml', '.yml'}


def load(name):
    return json.loads((EVAL / name).read_text(encoding='utf-8'))


def freeze_tool():
    spec = importlib.util.spec_from_file_location('eval_freeze', EVAL / 'freeze.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalized(text):
    return ' '.join(text.casefold().split())


def nulls(value, path=''):
    if value is None:
        return [path or '<root>']
    if isinstance(value, dict):
        return [found for key, item in value.items() for found in nulls(item, f'{path}.{key}')]
    if isinstance(value, list):
        return [found for index, item in enumerate(value) for found in nulls(item, f'{path}[{index}]')]
    return []


PROTOCOL = load('protocol.json')
HELDOUT = load('heldout.json')['cases']
CALIBRATION = load('calibration.json')['cases']
BASELINES = load('baselines.json')
BY_ID = {case['case_id']: case for case in HELDOUT}
VOCABULARY = PROTOCOL['case_vocabulary']


def development_cases():
    cases = list(CALIBRATION)
    for path in sorted((EVAL / 'development').glob('*.json')):
        cases += json.loads(path.read_text(encoding='utf-8'))['cases']
    return cases


class HeldOutDatasetTests(unittest.TestCase):
    def test_exactly_thirty_unique_cases(self):
        self.assertEqual(len(HELDOUT), 30)
        self.assertEqual(PROTOCOL['dataset_mix']['heldout_total'], 30)
        self.assertEqual(len(BY_ID), 30)
        self.assertEqual(len({case['request']['request_id'] for case in HELDOUT}), 30)
        self.assertEqual(len({normalized(case['buyer_text']) for case in HELDOUT}), 30)

    def test_mix_matches_the_frozen_declaration(self):
        mix = PROTOCOL['dataset_mix']
        for field in ('category', 'entry_path', 'market', 'language', 'designed_outcome'):
            with self.subTest(field=field):
                self.assertEqual(Counter(case[field] for case in HELDOUT), Counter(mix[field]))
        self.assertEqual(Counter(case['request']['intent']['kind'] for case in HELDOUT), Counter(mix['intent_kind']))

    def test_phones_and_laptops_are_emphasized(self):
        counts = Counter(case['category'] for case in HELDOUT)
        others = max(counts[name] for name in ('tablet', 'monitor', 'audio'))
        self.assertGreater(counts['phone'], others)
        self.assertGreater(counts['laptop'], others)
        self.assertGreaterEqual(counts['phone'] + counts['laptop'], 16)

    def test_every_category_has_both_paths_and_international_coverage(self):
        for category in VOCABULARY['category']:
            cases = [case for case in HELDOUT if case['category'] == category]
            with self.subTest(category=category):
                self.assertEqual({case['entry_path'] for case in cases}, {'exact', 'needs'})
                self.assertIn('international', {case['market'] for case in cases})

    def test_arabic_and_arabizi_cover_both_entry_paths(self):
        for language in ('ar', 'arabizi'):
            with self.subTest(language=language):
                self.assertEqual({case['entry_path'] for case in HELDOUT if case['language'] == language},
                                 {'exact', 'needs'})

    def test_language_labels_match_the_script(self):
        for case in HELDOUT:
            with self.subTest(case=case['case_id']):
                has_arabic = bool(ARABIC.search(case['buyer_text']))
                self.assertEqual(has_arabic, case['language'] == 'ar')
                # Arabizi writes Lebanese Arabic in Latin letters, with digits for some sounds (3 = ain, 2 = hamza).
                words = set(re.findall(r'[a-z0-9]+', case['buyer_text'].casefold()))
                self.assertEqual(bool(words & ARABIZI_WORDS), case['language'] == 'arabizi')
                if case['language'] == 'arabizi':
                    self.assertRegex(case['buyer_text'].casefold(), ARABIZI)

    def test_every_request_validates_against_the_v2_contract(self):
        for case in HELDOUT:
            with self.subTest(case=case['case_id']):
                request = v2.ShoppingRequest.model_validate(case['request'])
                self.assertIsInstance(v2.read_request(case['request']), v2.ShoppingRequest)
                self.assertEqual(case['request']['schema_version'], 2)
                self.assertEqual(request.revision, 1)

    def test_case_labels_agree_with_the_request(self):
        kinds = VOCABULARY['entry_path']
        for case in HELDOUT:
            request = v2.ShoppingRequest.model_validate(case['request'])
            intent = request.intent
            with self.subTest(case=case['case_id']):
                self.assertEqual(case['category'], request.requirements.category)
                self.assertIn(intent.kind, kinds[case['entry_path']])
                typed = {'exact_model': getattr(intent, 'model', None), 'needs': getattr(intent, 'description', None),
                         'product_link': str(getattr(intent, 'url', ''))}[intent.kind]
                self.assertEqual(typed, case['buyer_text'], 'the request carries the buyer text as the UI does')
                self.assertEqual(bool(case['target_products']), case['entry_path'] == 'exact')

    def test_default_scope_and_explicit_international_opt_in(self):
        for case in HELDOUT:
            scope = v2.ShoppingRequest.model_validate(case['request']).market_scope
            with self.subTest(case=case['case_id']):
                if case['market'] == 'domestic':
                    self.assertFalse(scope.include_international)
                    self.assertEqual(scope.international_markets, ())
                else:
                    self.assertTrue(scope.include_international)
                    self.assertTrue(scope.international_markets)

    def test_new_condition_stays_the_default_unless_the_case_tests_condition(self):
        for case in HELDOUT:
            conditions = v2.ShoppingRequest.model_validate(case['request']).allowed_conditions
            with self.subTest(case=case['case_id']):
                if 'condition_evidence' in case['scenarios']:
                    self.assertIn('refurbished', conditions)
                else:
                    self.assertEqual(conditions, ('new',))

    def test_scenarios_use_the_vocabulary_and_cover_every_required_trap(self):
        used = Counter(tag for case in HELDOUT for tag in case['scenarios'])
        self.assertLessEqual(set(used), set(VOCABULARY['scenarios']))
        for tag in VOCABULARY['required_heldout_scenarios']:
            self.assertGreater(used[tag], 0, tag)
        for case in HELDOUT:
            with self.subTest(case=case['case_id']):
                self.assertTrue(case['scenarios'])
                self.assertTrue(case['hard_constraints'])
                self.assertTrue(case['interpretation_checks'])
                self.assertEqual(case['designed_outcome'] == 'no_match', 'no_match' in case['scenarios'])
                self.assertEqual(case['market'] == 'international', 'international_route' in case['scenarios'])
                if 'clarification_expected' in case['scenarios']:
                    self.assertTrue(case['clarification_answer'])

    def test_heldout_text_is_not_copied_into_app_inputs(self):
        # Request IDs, links, needs descriptions and non-English texts identify a copied case. English
        # exact-model texts are plain product names that retailer catalogs may legitimately contain.
        needles = {case['request']['request_id'] for case in HELDOUT}
        needles |= {case['request']['intent']['url'] for case in HELDOUT if 'url' in case['request']['intent']}
        needles |= {case['buyer_text'] for case in HELDOUT if case['entry_path'] == 'needs' or case['language'] != 'en'}
        self.assertGreaterEqual(len(needles), 30 + 2 + 13)
        found = []
        for folder in APP_INPUTS:
            for path in (ROOT / folder).rglob('*'):
                if path.is_file() and path.suffix in TEXT_SUFFIXES:
                    text = path.read_text(encoding='utf-8', errors='ignore')
                    found += [(str(path.relative_to(ROOT)), needle) for needle in needles if needle in text]
        self.assertEqual(found, [], 'Held-out cases must not seed app inputs or fixtures; '
                         'use a calibration or development case instead.')


class CalibrationTests(unittest.TestCase):
    def test_calibration_is_a_separate_valid_set(self):
        self.assertGreaterEqual(len(CALIBRATION), PROTOCOL['dataset_mix']['calibration_minimum'])
        self.assertEqual({case['category'] for case in CALIBRATION}, set(VOCABULARY['category']))
        self.assertEqual({case['entry_path'] for case in CALIBRATION}, {'exact', 'needs'})
        self.assertEqual({case['language'] for case in CALIBRATION}, set(VOCABULARY['language']))
        for case in CALIBRATION:
            with self.subTest(case=case['case_id']):
                v2.ShoppingRequest.model_validate(case['request'])

    def test_no_case_text_link_or_product_overlaps_the_heldout_set(self):
        def keys(cases):
            return {
                'case_id': {case['case_id'] for case in cases},
                'request_id': {case['request']['request_id'] for case in cases},
                'buyer_text': {normalized(case['buyer_text']) for case in cases},
                'target_product': {normalized(name) for case in cases for name in case['target_products']},
                'url': {case['request']['intent']['url'] for case in cases if 'url' in case['request']['intent']},
            }
        heldout, development = keys(HELDOUT), keys(development_cases())
        for field, values in heldout.items():
            with self.subTest(field=field):
                self.assertFalse(values & development[field])


class BaselineTests(unittest.TestCase):
    def test_subsets_are_drawn_from_the_heldout_set(self):
        subsets = BASELINES['subsets']
        local, assistant = subsets['local_external'], subsets['assistant']
        self.assertLessEqual(set(local), set(BY_ID))
        self.assertLessEqual(set(assistant), set(BY_ID))
        self.assertLessEqual(set(local), set(assistant))
        self.assertEqual(len(set(local)), len(local))

    def test_local_external_subset_is_domestic_and_covers_the_required_traps(self):
        cases = [BY_ID[case_id] for case_id in BASELINES['subsets']['local_external']]
        self.assertTrue(all(case['market'] == 'domestic' for case in cases))
        phones_laptops = sum(case['category'] in {'phone', 'laptop'} for case in cases)
        self.assertGreaterEqual(phones_laptops / len(cases), 0.8)
        tags = Counter(tag for case in cases for tag in case['scenarios'])
        for tag in ('variant_trap', 'needs_selection', 'missing_cost_trap', 'availability_trap'):
            self.assertGreater(tags[tag], 0, tag)
        self.assertTrue(any(case['entry_path'] == 'exact' and 'variant_trap' in case['scenarios'] for case in cases))

    def test_subset_inputs_and_orders_cover_exactly_their_subsets(self):
        local, assistant = BASELINES['subsets']['local_external'], BASELINES['subsets']['assistant']
        self.assertEqual(set(BASELINES['subset_inputs']), set(assistant))
        for case_id, inputs in BASELINES['subset_inputs'].items():
            request = BY_ID[case_id]['request']
            fixed = inputs['fixed_requirements']
            with self.subTest(case=case_id):
                self.assertEqual(inputs['buyer_text'], BY_ID[case_id]['buyer_text'])
                # Every number the buyer fixed in the request reaches the external operators unchanged.
                requirements = request['requirements']
                numbers = [value for key, value in requirements.items() if key != 'category'
                           and not isinstance(value, (bool, str, list)) and value is not None]
                numbers += [requirements['min_diagonal_inches']] if 'min_diagonal_inches' in requirements else []
                numbers += [f"{int(m['amount']):,} {m['currency']}" for m in (request.get('budget') or {}).values() if m]
                for number in numbers:
                    self.assertRegex(fixed, rf'(?<![\d.,]){re.escape(str(number))}(?![\d.,]*\d)')
                for key in ('cpu', 'gpu'):
                    if requirements.get(key):
                        self.assertIn(f"{requirements[key]} {'processor' if key == 'cpu' else 'graphics'};", fixed)
                if requirements.get('sim') == 'physical+esim':
                    self.assertIn('one physical nano-SIM plus eSIM', fixed)
                city = (request.get('fulfillment') or {}).get('destination', {}).get('city')
                self.assertEqual(inputs['destination'], city or 'Lebanon')
                self.assertEqual('only sellers in Lebanon' in fixed, BY_ID[case_id]['market'] == 'domestic')
        sides = {'techscout', 'lebtech', 'onesouq', 'manual-shopping'}
        orders = BASELINES['paired_sessions']['orders']
        self.assertEqual(set(orders), set(local))
        for case_id, order in orders.items():
            self.assertEqual(sorted(order), sorted(sides), case_id)
        firsts = Counter(order[0] for order in orders.values())
        self.assertEqual(set(firsts), sides, 'every side goes first in some session')
        self.assertEqual(set(BASELINES['paired_sessions']['assistant_orders']), set(assistant))

    def test_run_order_is_the_seeded_shuffle_of_all_cases(self):
        order = BASELINES['run_order']
        for key, seed in (('warm_repetition_1', 78), ('warm_repetition_2', 79), ('warm_repetition_3', 80), ('cold', 178)):
            expected = sorted(BY_ID)
            random.Random(seed).shuffle(expected)
            with self.subTest(order=key):
                self.assertEqual(order[key], expected)

    def test_configurations_are_complete(self):
        configurations = {item['id']: item for item in BASELINES['configurations']}
        self.assertEqual(len(configurations), len(BASELINES['configurations']))
        for required in ('full-warm', 'single-agent-warm', 'fixed-heuristic-warm', 'full-cold',
                         'lebtech', 'onesouq', 'manual-shopping', 'chatgpt-browsing', 'claude-browsing'):
            self.assertIn(required, configurations)
        for item in configurations.values():
            with self.subTest(configuration=item['id']):
                self.assertGreater(item['repetitions'], 0)
                self.assertIn(item['cases'], {'all', *BASELINES['subsets']})
                if item['kind'] == 'automated':
                    self.assertEqual(item['cases'], 'all')
        self.assertEqual(configurations['full-cold']['corpus'], 'cold')


class MetricTests(unittest.TestCase):
    def test_metrics_have_ids_units_and_denominators(self):
        metrics = PROTOCOL['metrics']
        ids = [metric['id'] for metric in metrics]
        self.assertEqual(len(ids), len(set(ids)))
        for metric in metrics:
            with self.subTest(metric=metric['id']):
                self.assertTrue(metric['unit'])
                self.assertTrue(any(key in metric for key in ('denominator', 'categories', 'definition')))
        for required in ('valid_offer_rate', 'request_success_rate', 'useful_result_success_rate', 'abstention_rate',
                         'answer_coverage', 'retailer_stage_counts', 'multi_seller_products',
                         'first_useful_display_seconds', 'verified_result_seconds', 'paired_price_outcomes',
                         'interpretation_accuracy', 'international_capability', 'external_outcome_distribution'):
            self.assertIn(required, ids)
        valid = next(metric for metric in metrics if metric['id'] == 'valid_offer_rate')
        self.assertEqual(valid['target'], {'operator': '>=', 'value': '0.95'})
        stages = next(metric for metric in metrics if metric['id'] == 'retailer_stage_counts')['definitions']
        self.assertEqual(list(stages), ['discovered', 'attempted', 'retrieved', 'qualifying'])
        self.assertEqual(list(stages), list(v2.CoverageCounts.model_fields))
        self.assertEqual(PROTOCOL['timing']['active_execution_limit_seconds'], 120)

    def test_the_specification_covers_every_metric_and_case(self):
        doc = DOC.read_text(encoding='utf-8')
        self.assertIn(f'Protocol version **{PROTOCOL["version"]}**', doc)
        for metric in PROTOCOL['metrics']:
            self.assertIn(f'`{metric["id"]}`', doc)
        rows = {cells[0]: cells for cells in (
            [cell.strip() for cell in line.strip().strip('|').split('|')]
            for line in doc.splitlines() if re.match(r'^\| H\d\d \|', line))}
        paths = {'exact_model': 'exact', 'product_link': 'exact (link)', 'needs': 'needs'}
        expected = {case['case_id']: [
            case['case_id'], case['category'], paths[case['request']['intent']['kind']], case['market'],
            case['language'], case['designed_outcome'], ', '.join(case['scenarios']),
            ', '.join(case['target_products']) or '(needs-based)'] for case in HELDOUT}
        self.assertEqual(rows, expected, 'The case table in docs/evaluation-protocol.md must match eval/heldout.json')
        self.assertIn('evaluation-protocol.md', (ROOT / 'docs' / 'README.md').read_text(encoding='utf-8'))


class ModelAndCorpusTests(unittest.TestCase):
    def test_openrouter_inference_and_embeddings_match_the_frozen_configuration(self):
        main = load('models.json')['main_evaluation']
        from eval.deployment import pinned_inference
        config = pinned_inference(api_key='offline-only').versions()
        self.assertEqual(main['access_path'], 'openrouter')
        self.assertEqual((main['provider'], main['model'], main['serving_provider']),
                         (config['provider'], config['model'], config['serving_provider']),
                         'config/evaluation.json no longer uses the frozen evaluation model. ' + HOW_TO_CHANGE)
        self.assertEqual(main['embedding']['model'], EMBEDDING_MODEL,
                         'app.retrieval no longer uses the frozen embedding model. ' + HOW_TO_CHANGE)
        for switch in ('automatic_model_substitution', 'provider_fallbacks', 'automatic_retries'):
            self.assertIs(main[switch], False, switch)

    def test_optional_experiments_are_labelled_apart(self):
        experiments = load('models.json')['optional_experiments']
        for item in experiments['declared']:
            self.assertTrue(item['label'].startswith('exp-'))
            self.assertNotEqual(item['label'], 'main')

    def test_cold_manifest_is_frozen_and_empty(self):
        cold = load('corpus/cold.json')
        self.assertEqual(cold['state'], 'frozen')
        self.assertEqual(set(cold['contents'].values()), {0})
        self.assertEqual(cold['sources'], [])
        self.assertEqual(cold['acquisition_cost']['spend_usd'], '0')

    def test_warm_template_asks_for_versions_costs_and_attestations(self):
        warm = load('corpus/warm.template.json')
        self.assertEqual(warm['state'], 'template')
        for field in ('corpus_version', 'index_version', 'processing_versions', 'snapshot', 'sources',
                      'category_totals', 'acquisition_cost', 'isolation', 'attestations'):
            self.assertIn(field, warm)
        self.assertEqual(set(warm['attestations']), set(warm['required_attestation_values']))
        self.assertEqual(set(warm['category_totals']), set(VOCABULARY['category']))

    def test_filled_pre_run_files_are_complete(self):
        warm_path, run_path = EVAL / 'corpus' / 'warm.json', EVAL / 'run-freeze.json'
        if warm_path.exists():
            warm = json.loads(warm_path.read_text(encoding='utf-8'))
            self.assertEqual(warm['state'], 'frozen')
            self.assertEqual(nulls(warm), [])
            self.assertEqual(warm['attestations'], warm['required_attestation_values'])
        if run_path.exists():
            run = json.loads(run_path.read_text(encoding='utf-8'))
            self.assertEqual(run['state'], 'frozen')
            self.assertEqual([path for path in nulls(run) if path != '.repetition_reduction_reason'], [])


class FreezeTests(unittest.TestCase):
    def test_frozen_files_match_their_recorded_hashes(self):
        self.assertEqual(freeze_tool().problems(), [], HOW_TO_CHANGE)

    def test_version_and_combined_hash_are_pinned(self):
        record = freeze_tool().load()
        self.assertEqual(record['version'], FROZEN_VERSION, HOW_TO_CHANGE)
        self.assertEqual(record['combined_sha256'], FROZEN_COMBINED_SHA256, HOW_TO_CHANGE)
        self.assertEqual(record['version'], PROTOCOL['version'])
        self.assertEqual(record['changelog'][-1]['version'], FROZEN_VERSION)

    def test_every_protocol_file_is_frozen(self):
        frozen = set(freeze_tool().load()['files'])
        expected = {'docs/evaluation-protocol.md', 'eval/README.md','config/evaluation.json'}
        # Results (#82) and extra development cases (#80) are added later and are not frozen protocol files.
        expected |= {path.relative_to(ROOT).as_posix() for path in EVAL.rglob('*.json')
                     if path.name != 'freeze.json' and path.relative_to(EVAL).parts[0] not in {'results', 'development'}}
        expected -= set(freeze_tool().PRE_RUN_FILES)
        self.assertEqual(frozen, expected)

    def test_a_changed_file_or_line_ending_is_handled(self):
        tool = freeze_tool()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in [*tool.load()['files'], tool.RECORD]:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, root / name)
            target = root / 'eval' / 'heldout.json'
            target.write_bytes(target.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
            self.assertEqual(tool.problems(root), [], 'CRLF and LF checkouts hash the same')
            target.write_bytes(target.read_bytes().replace(b'Saida', b'Sidon'))
            self.assertIn(f'protocol {FROZEN_VERSION}: eval/heldout.json changed since it was frozen', tool.problems(root))
            with self.assertRaises(ValueError):
                tool.bump('1.0.0', 'same version', root)
            tool.bump('1.1.1', 'Test change', root, today='2026-11-17')
            self.assertEqual(tool.problems(root), [])
            self.assertEqual(tool.load(root)['changelog'][-1],
                             {'version': '1.1.1', 'date': '2026-11-17', 'change': 'Test change'})
            with self.assertRaises(ValueError):
                tool.pre_run(root)  # warm.json and run-freeze.json do not exist yet
            for name in tool.PRE_RUN_FILES:
                (root / name).write_text('{"state": "frozen"}', encoding='utf-8')
            tool.pre_run(root, now='2026-11-30T12:00:00+00:00')
            self.assertEqual(tool.problems(root), [])
            with self.assertRaises(ValueError):
                tool.pre_run(root)
            with self.assertRaises(ValueError):
                tool.bump('1.1.0', 'after held-out runs', root)


if __name__ == '__main__':
    unittest.main()

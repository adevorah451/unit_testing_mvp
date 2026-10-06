"""Matched revision arms: only cached own-candidate feedback visibility differs.

The historical training-only TestRepair implementation and artifacts remain untouched.
"""
from __future__ import annotations

from typing import Any
import hashlib
import json
import re
from pathlib import Path
from .. import prompts, model as model_mod, sandbox
from ..data import load_records
from .test_repair import feedback_summary, parse_repair, repair_schema
from .unit_testing import spaces_from, suite_source, test_names_in, _call

from .unit_testing import UnitTesting


def revision_prompt(
    task, source: str, inputs: list, diagnostic: dict, feedback_visible: bool
) -> str:
    """Render the identical revision contract with actual feedback or a withheld marker only."""
    # Never pass free-form exception strings: they may echo candidate source or output.
    visible = {k: diagnostic[k] for k in ('complete', 'n_records', 'n_expected') if k in diagnostic}
    parse_failed = diagnostic.get('parse_error') is not None
    static = {'source_parse_failed':parse_failed, 'requested_test_count':10,
              'parsed_test_count':None if parse_failed else len(test_names_in(source))}
    static['test_count_complete'] = None if parse_failed else static['parsed_test_count'] == 10
    visible['tests'] = None if diagnostic['tests'] is None else [
        {'test': row['test'], 'counts': row.get('counts'),
         'events': [{k: event[k] for k in ('prop', 'i', 'outcome')} for event in row['events']]}
        for row in diagnostic['tests']]
    blind = task.blind()
    payload = {'task': blind, 'initial_suite': source, 'static_validation':static,
               'fixed_inputs': [{'i': i, 'input': x} for i, x in enumerate(inputs)],
               'execution_diagnostics': visible if feedback_visible else 'WITHHELD_BY_DESIGN'}
    return prompts.render('second_revision_v1.txt',
        invoke_contract=prompts.invoke_contract(blind['io_mode'], blind['entry_point']),
        framing_rule=prompts.render(prompts.FRAMING_RULE_FILES['traceable_v1']),
        resolve_rule=prompts.render(prompts.resolve_rule_file('with')),
        worked_example=prompts.render(prompts.worked_example_file('repair_tests_v1.txt', blind['io_mode'])),
        payload=json.dumps(payload, sort_keys=True))


class SecondRevision(UnitTesting):
    """One extra call per eligible fresh holdout candidate; never retry records or fall back."""
    protocol = "second_revision"

    def __init__(self, *, baseline_run: str, source_bundle: str,
                 source_bundle_sha256: str, feedback_visible: bool,
                 max_candidates: int, **kwargs: Any):
        """Freeze population cap, source link, and the sole revision-arm intervention."""
        if not baseline_run or not source_bundle or not re.fullmatch('[0-9a-f]{64}', source_bundle_sha256):
            raise ValueError('source run, bundle and exact SHA256 are required')
        if type(feedback_visible) is not bool or type(max_candidates) is not int or max_candidates < 1:
            raise ValueError('explicit feedback visibility and positive candidate cap required')
        if kwargs.get('cache') is None:
            kwargs['cache'] = False
        kwargs.setdefault('max_tokens', 8192)
        kwargs.setdefault('resolve', 'with')
        kwargs.setdefault('test_gen_prompt', 'traceable_v1')
        kwargs.setdefault('code_visible', False)
        super().__init__(baseline_run=baseline_run, source_bundle=source_bundle,
                         source_bundle_sha256=source_bundle_sha256, feedback_visible=feedback_visible,
                         max_candidates=max_candidates, **kwargs)
        if (self.code_visible or self.critique or self.critique_informed or self.n_tests != 10
                or self.framing != 'traceable_v1' or self.resolve != 'with' or self.cache):
            raise ValueError('fixed revision design: hidden code, no critique/cache, exactly10 traceable-with tests')
        if self.data.train or not self.data.test or self.total > max_candidates:
            raise ValueError('fresh test-only population must fit preregistered candidate cap')
        self.baseline_run = baseline_run
        self.source_bundle = source_bundle
        self.source_bundle_sha256 = source_bundle_sha256
        self.feedback_visible = feedback_visible

    def _runtime(self):
        """No HTTP retry: one revision attempt, including transport failures."""
        return super()._runtime().model_copy(update={'http_retries': 0})

    def prepare(self, data):
        """Validate complete immutable source/inputs/feedback linkage before resolving credentials."""
        byte_hash = lambda raw: hashlib.sha256(raw).hexdigest()
        object_hash = lambda value: byte_hash(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())
        raw_bundle = Path(self.source_bundle).read_bytes()
        if byte_hash(raw_bundle) != self.source_bundle_sha256:
            raise ValueError('source bundle hash changed')
        bundle = json.loads(raw_bundle)
        paths = {'dataset_sha256': Path(self.data_path),
                 'source_config_sha256': Path('runs')/self.baseline_run/'config.json',
                 'source_records_sha256': Path('runs')/self.baseline_run/'records.jsonl',
                 'input_records_sha256': Path('runs')/self.triggers/'records.jsonl'}
        if set(bundle) != {'schema_version', 'candidates', *paths} or bundle['schema_version'] != 1:
            raise ValueError('unexpected source bundle shape/version')
        for key, path in paths.items():
            if byte_hash(path.read_bytes()) != bundle[key]:
                raise ValueError(f'{key} hash changed')
        baseline = json.loads(paths['source_config_sha256'].read_text())
        baseline = baseline | baseline['params']
        own = self.config() | self.config()['params']
        for key in ('model', 'seed', 'n_tests', 'max_tokens', 'reasoning', 'call_seconds',
                    'sandbox_seconds', 'docker_image', 'triggers', 'resolve', 'test_gen_prompt'):
            if baseline[key] != own[key]:
                raise ValueError(f'baseline/revision settings differ: {key}')
        if (baseline['protocol'] != 'unit_testing' or baseline['code_visible'] is not True
                or baseline['critique'] or baseline['critique_informed'] or baseline['runs'] != 1
                or baseline['data'] != self.data_path):
            raise ValueError('baseline must be the frozen code-visible one-turn UnitTesting policy')
        sources = load_records(self.baseline_run)
        inputs = load_records(self.triggers)
        wanted = {c.candidate_id for _, c in data.candidates()}
        by_id = {r['candidate_id']: r for r in sources}
        input_by_id = {r['candidate_id']: r for r in inputs}
        if (len(sources) != len(wanted) or set(by_id) != wanted or len(inputs) != len(wanted)
                or set(input_by_id) != wanted or set(bundle['candidates']) != wanted):
            raise ValueError('duplicate, missing or unexpected source/input/bundle candidate')
        self.trigger_space, unusable = spaces_from(self.triggers, data)
        if unusable:
            raise ValueError('both revisions require the same reviewed nonempty candidate inputs')
        self.revision_context = {}
        for task, candidate in data.candidates():
            cid = candidate.candidate_id
            record, row = by_id[cid], bundle['candidates'][cid]
            if set(row) != {'source_record_sha256','inputs_sha256','code_sha256','suite_sha256','result'}:
                raise ValueError('unexpected source candidate fields')
            if (
                record['task_id'] != task.task_id
                or record['split'] != 'test'
                or input_by_id[cid]['task_id'] != task.task_id
                or input_by_id[cid]['split'] != 'test'
            ):
                raise ValueError('source/input task or split mismatch')
            space = self.trigger_space[cid]
            if (row['source_record_sha256'] != object_hash(record) or row['inputs_sha256'] != object_hash(space)
                    or row['code_sha256'] != byte_hash(candidate.code.encode())):
                raise ValueError('candidate source/input/code hash changed')
            if len(record['calls']) > 1:
                raise ValueError('source must contain at most one original authoring response')
            raw = record['calls'][0]['raw'] if record['calls'] else ''
            source, parse_error = suite_source(raw)
            if record['tests_src'] is not None and source != record['tests_src']:
                raise ValueError('recorded suite differs from original response')
            if row['suite_sha256'] != (None if source is None else byte_hash(source.encode())):
                raise ValueError('initial suite hash changed')
            provenance = {key: row[key] for key in row if key != 'result'}
            if not raw:
                self.revision_context[cid] = {'eligible':False, 'reason':'no saved baseline response',
                                             'provenance':provenance}
                continue
            diagnostic = feedback_summary(row['result'], parse_error, source, space)
            self.revision_context[cid] = {'eligible':True, 'source':source if source is not None else raw,
                'diagnostic':diagnostic, 'provenance':provenance,
                'stratum':'parse_recovery' if source is None else
                          'source_failure' if record['failed'] else
                          'complete_source' if diagnostic['complete'] else 'partial_source'}
        model_mod.resolve(self._runtime())

    def score(self, task, candidate):
        """One schema-constrained revision; retain calls and failures and never repair a repair."""
        context = self.revision_context[candidate.candidate_id]
        metadata = {'eligible':context['eligible'], 'baseline_run':self.baseline_run,
                    'source_hashes':context['provenance'], 'source_bundle_sha256':self.source_bundle_sha256,
                    'feedback_visible':self.feedback_visible, 'revision_version':1,
                    'assertion_reach_measured':False}
        if not context['eligible']:
            return self._unmeasured([], 'infra', context['reason']) | metadata
        space = self.trigger_space[candidate.candidate_id]
        prompt = revision_prompt(task, context['source'], space, context['diagnostic'], self.feedback_visible)
        completion = model_mod.complete_sync(model_mod.resolve(self._runtime()), prompt,
                                             'property_gen', repair_schema(self.n_tests))
        calls = [_call(prompt, completion)]
        parsed = parse_repair(completion.text, self.n_tests)
        metadata |= {'source_stratum':context['stratum'], 'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                     'abstained':parsed['abstained'], 'revision_rationale':parsed['rationale'],
                     'unique_test_bodies':parsed['unique_bodies']}
        if parsed['error'] is not None:
            return self._unmeasured(calls, 'model', parsed['error']) | metadata
        try:
            result = sandbox.run_raw(task, candidate.code, parsed['tests_src'], list(space),
                                     timeout_s=self.sandbox_seconds, isolation=sandbox.Isolation.DOCKER,
                                     docker_image=self.docker_image)
        except Exception as error:
            return self._unmeasured(calls, 'infra', f'sandbox: {type(error).__name__}: {error}') | metadata | {
                'tests_src':parsed['tests_src'], 'test_names':parsed['test_names'], 'execution':None, 'complete':False}
        if result['ok'] and (len(result['props']) != self.n_tests or set(result['props']) != set(parsed['test_names'])
                             or result['n_expected'] != self.n_tests * len(space)):
            return self._unmeasured(calls, 'model', 'sandbox callable test set/grid differs from declared suite') | metadata | {
                'tests_src':parsed['tests_src'], 'test_names':parsed['test_names'], 'execution':result, 'complete':False}
        return self._verdict(calls, parsed['tests_src'], parsed['test_names'], space, result) | metadata | {
            'execution':result, 'complete':result['complete']}

import os
import uuid
import unittest
from datetime import datetime
from backend.infra.storage import sequence_repository as repo

def test_cache_version_tracks_app_source_after_directory_move(tmp_path, monkeypatch):
    source = tmp_path / 'APP/backend/agent/planner.py'
    source.parent.mkdir(parents=True)
    source.write_text('version = 1\n')
    monkeypatch.setattr(repo, 'ROOT', tmp_path)
    repo.algorithm_version.cache_clear()
    try:
        first = repo.algorithm_version()
        source.write_text('version = 2\n')
        repo.algorithm_version.cache_clear()
        assert repo.algorithm_version() != first
        model = tmp_path / 'research/third_party/trnaviz/engine/all-num.cm'
        model.parent.mkdir(parents=True)
        model.write_bytes(b'covariance model')
        repo.algorithm_version.cache_clear()
        with_model = repo.algorithm_version()
        model.write_bytes(b'updated covariance model')
        repo.algorithm_version.cache_clear()
        assert repo.algorithm_version() != with_model
    finally:
        repo.algorithm_version.cache_clear()

class RepositoryUnitTests(unittest.TestCase):
    def test_rna_normalization(self):
        self.assertEqual(repo.normalize(' acG\n t\t'), 'ACGU')

    def test_draft_timestamp_and_timezone(self):
        self.assertEqual(repo.timestamp({'drafted_at':'2026-10-11T17:05:04+0800'}),datetime(2026,10,11,9,5,4))

    def test_credentials_redacted_recursively(self):
        self.assertEqual(repo.safe_config({'nested':{'API_KEY':'private'},'model':'m'}),{'nested':{'API_KEY':'[redacted]'},'model':'m'})

    def test_technical_failure_detection(self):
        self.assertTrue(repo.has_technical_error({'agent':{'events':[{'error_type':'TimeoutError'}]}}))
        self.assertFalse(repo.has_technical_error({'agent':{'events':[{'status':'accepted'}]}}))

@unittest.skipUnless(os.getenv('TRNA_DB_INTEGRATION_TEST')=='1','requires explicitly enabled MySQL integration tests')
class RepositoryDatabaseTests(unittest.TestCase):
    def test_latest_curation_cache_and_failures(self):
        marker=uuid.uuid4().hex
        name='__repository_test_'+marker
        sequence='ACGU'+marker.upper()
        options={'test':marker}
        def compute(callback):
            callback({'stage':'test','message':'integration test'})
            return ('test','csv\n',[],{})
        try:
            for date,status in [('2026-10-11T17:00:00+0800','accepted_gold'),('2026-09-01T17:00:00+0800','unsure')]:
                repo.put_annotation({'seqname':name,'curated_at':date,'status':status,'bases_by_residue':list(sequence)})
            self.assertEqual(repo.latest_annotations()[name]['status'],'accepted_gold')
            first=repo.execute_cached(sequence,options,{},compute)
            self.assertFalse(first[3]['cache']['hit'])
            def should_not_run(callback):raise AssertionError('Cache miss')
            second=repo.execute_cached(sequence,options,{},should_not_run)
            self.assertTrue(second[3]['cache']['hit'])
            self.assertEqual(first[3]['cache']['run_id'],second[3]['cache']['run_id'])
            options['version']=2
            third=repo.execute_cached(sequence,options,{},compute)
            self.assertFalse(third[3]['cache']['hit'])
            def failed(callback):raise TimeoutError('synthetic timeout')
            options['version']=3
            with self.assertRaises(TimeoutError):repo.execute_cached(sequence,options,{},failed)
            retry=repo.execute_cached(sequence,options,{},compute)
            self.assertFalse(retry[3]['cache']['hit'])
            options['version']=4
            def fallback(callback):return ('test','csv\n',[],{'trace':[{'error_type':'TimeoutError'}]})
            repo.execute_cached(sequence,options,{},fallback)
            recovered=repo.execute_cached(sequence,options,{},compute)
            self.assertFalse(recovered[3]['cache']['hit'])
        finally:
            key=__import__('hashlib').sha256(repo.normalize(sequence).encode()).hexdigest()
            with repo.connection() as c:
                with c.cursor() as x:
                    x.execute('DELETE k FROM trna_cache k JOIN trna_computations r ON r.run_id=k.run_id WHERE r.sequence_hash=%s',(key,))
                    x.execute('DELETE FROM trna_requests WHERE sequence_hash=%s',(key,))
                    x.execute('DELETE FROM trna_computations WHERE sequence_hash=%s',(key,))
                    x.execute('DELETE FROM trna_annotation_heads WHERE seqname=%s',(name,))
                    x.execute('DELETE FROM trna_annotations WHERE seqname=%s',(name,))
                    x.execute('DELETE FROM trna_sequence_aliases WHERE seqname=%s',(name,))
                    x.execute('DELETE FROM trna_sequences WHERE sequence_hash=%s',(key,))
                c.commit()

if __name__=='__main__':unittest.main()

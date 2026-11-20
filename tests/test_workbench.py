import json, tempfile, unittest, threading, urllib.request, urllib.error
from pathlib import Path
from workbench import Index, Stale, exact_excerpt, make_server

class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.health=self.root/'health.json'; self.sync=self.root/'sync.json'
        self.put('BRAIN-MAP.md','# Atlas\n[[Projects/Alpha Roadmap#Next steps]]\n'); self.put('Projects/Alpha Roadmap.md','---\naliases: [Northstar, Alpha]\n---\n# Roadmap\n## Next steps\nCerulean signal in the pilot.\n[[Docs/Receipt]]\n[[Status]]\n'); self.put('Docs/Receipt.md','# Receipt\nVerified outcome.\n'); self.put('A/Status.md','# Status\n'); self.put('B/Status.md','# Status\n'); self.index=Index(self.root,self.health,self.sync,expect_maps=True); self.addCleanup(self.index.close)
    def put(self,name,data):
        p=self.root/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(data,encoding='utf-8'); return p
    def test_alias_and_section_line_no_body_leak(self):
        a=self.index.search('Northstar')['hits'][0]; self.assertEqual(a['path'],'Projects/Alpha Roadmap.md')
        b=self.index.search('cerulean signal')['hits'][0]; self.assertEqual(b['line'],5); self.assertEqual(b['heading'],'Next steps'); self.assertNotIn('Cerulean signal in the pilot',json.dumps(b))
        self.assertFalse(self.index.search('qzxnonexistent')['hits'])
    def test_evidence_source_and_cursor(self):
        p='Projects/Alpha Roadmap.md'; v=self.index.read(p,6,0,40); self.assertEqual(v['start_line'],6); self.assertEqual(v['heading'],'Next steps'); self.assertEqual(len(v['sha256']),64); self.assertTrue(v['more']); self.assertLessEqual(len(v['text']),40)
        e=self.index.read(p,*v['next'],40); self.assertNotEqual(e['text'],v['text'])
        p=self.put('New.md','a'*150+'\n'); self.index.rebuild(); a=self.index.read('New.md',1,0,40); self.assertEqual(a['next'],[1,40]); self.assertEqual(self.index.read('New.md',*a['next'],40)['start_line'],1)
    def test_graph_no_guess_and_directed_paths(self):
        d=self.index.read('Projects/Alpha Roadmap.md',1,0,400)
        self.assertEqual(d['outlinks'][0]['path'],'Docs/Receipt.md'); self.assertEqual(d['backlinks'][0]['path'],'BRAIN-MAP.md')
        self.assertEqual(d['unresolved'][0]['kind'],'ambiguous')
        self.assertEqual(self.index.path('BRAIN-MAP.md','Docs/Receipt.md')['path'],['BRAIN-MAP.md','Projects/Alpha Roadmap.md','Docs/Receipt.md'])
        self.assertFalse(self.index.path('Docs/Receipt.md','BRAIN-MAP.md')['path'])
    def test_stale_rename_and_refresh(self):
        p='Docs/Receipt.md'; old=self.index.id; self.put(p,'# Changed\n'); self.assertTrue(self.index.health()['index']['drift']);
        with self.assertRaises(Stale): self.index.read(p,1,0,400)
        self.index.rebuild(); self.assertNotEqual(old,self.index.id); self.assertIn('Changed',self.index.read(p,1,0,400)['text']);
        (self.root/p).rename(self.root/'Docs/Moved.md'); self.assertTrue(self.index.health()['index']['drift'])
    def test_stale_pathway_rejected(self):
        self.put('Projects/Alpha Roadmap.md','# Changed\n');
        with self.assertRaises(Stale): self.index.path('BRAIN-MAP.md','Docs/Receipt.md')
    def test_negative_pathway_requires_current_inventory(self):
        self.put('Docs/Receipt.md','# Receipt\n[[BRAIN-MAP]]\n')
        with self.assertRaises(Stale): self.index.path('A/Status.md','B/Status.md')
    def test_added_duplicate_cannot_leave_stale_route_verified(self):
        self.put('BRAIN-MAP.md','# Atlas\n[[Alpha Roadmap]]\n'); self.index.rebuild()
        self.assertTrue(self.index.path('BRAIN-MAP.md','Docs/Receipt.md')['path'])
        self.put('Duplicate/Alpha Roadmap.md','# Another note\n')
        with self.assertRaises(Stale): self.index.path('BRAIN-MAP.md','Docs/Receipt.md')
    def test_case_exact_root_precedes_duplicate_stems(self):
        self.put('Root Record.md','# Designated root\n'); self.put('Fork/Root Record.md','# Duplicate\n');
        self.put('Source.md','# Source\n[[Root Record]]\n'); self.index.rebuild()
        self.assertEqual(self.index.read('Source.md')['outlinks'][0]['path'],'Root Record.md')
    def test_exact_basename_case_precedes_casefolded_namesake(self):
        self.put('One/Ledger.md','# First\n'); self.put('Two/ledger.md','# Second\n')
        self.put('Source.md','[[ledger]]\n[[LEDGER]]\n'); self.index.rebuild()
        source=self.index.read('Source.md'); self.assertEqual([x['path'] for x in source['outlinks']],['Two/ledger.md'])
        self.assertEqual(source['unresolved'][0]['line'],2)
    def test_health_returns_bounded_actionable_graph_warnings(self):
        g=self.index.health()['graph']; self.assertEqual(g['warning_examples'],[{'source':'Projects/Alpha Roadmap.md','line':8,'target':'Status','kind':'ambiguous'}])
    def test_read_with_digest_needs_only_exact_file_and_fails_when_edited(self):
        p='Docs/Receipt.md'; hit=self.index.search('verified outcome')['hits'][0]
        read=exact_excerpt(self.root,p,1,0,400,hit['sha256']); self.assertIn('Verified outcome',read['text'])
        with self.assertRaises(ValueError):exact_excerpt(self.root,p,1,0,400,'bad-digest')
        self.put(p,'# Receipt\nChanged.\n')
        with self.assertRaises(Stale):exact_excerpt(self.root,p,1,0,400,hit['sha256'])
    def test_malformed_link_does_not_prevent_rebuild(self):
        self.put('Bad.md','# Bad\n[[http://[oops]]\n[bad](http://[oops)\n[[../../outside.md]]\n')
        self.index.rebuild(); self.assertTrue(self.index.search('bad')['hits'])
    def test_refuses_outside_symlink_and_hardlink(self):
        outside=self.root.parent/(self.root.name+'-outside.md'); outside.write_text('outsidesecret'); self.addCleanup(lambda:outside.unlink(missing_ok=True)); (self.root/'Z.md').symlink_to(outside)
        import os; os.link(outside,self.root/'Hard.md'); self.index.rebuild(); self.assertFalse(self.index.search('outsidesecret')['hits'])
        for path in ['Z.md','Hard.md','../outside.md','/etc/passwd']:
            with self.assertRaises((ValueError, KeyError, OSError)): self.index.read(path,1,0,100)
    def test_audit_scope_and_joined_sync_receipts(self):
        self.health.write_text(json.dumps({'runId':'a','status':'degraded','checkedAtLocal':'2026-11-18T00:00:00-05:00'})); self.sync.write_text(json.dumps({'runId':'b','files':[{'action':'would-sync'}]})); h=self.index.health(); self.assertFalse(h['maintenance']['same_run']); self.assertIsNone(h['maintenance']['pending']); self.assertTrue(h['graph']['missing_roots']);
        self.sync.write_text(json.dumps({'runId':'a','files':[{'action':'would-sync'},{'action':'unchanged-since-last-sync'}]})); self.assertEqual(self.index.health()['maintenance']['pending'],1)
    def test_vault_never_assumes_external_receipts(self):
        other=Index(self.root); self.addCleanup(other.close)
        m=other.health()['maintenance']; self.assertEqual(m['status'],'not configured'); self.assertFalse(m['same_run']); self.assertIsNone(m['pending'])
    def test_http_requires_host_token_origin_and_no_side_effect_by_get(self):
        server=make_server(self.index,0,'test-session-token'); t=threading.Thread(target=server.serve_forever,daemon=True); t.start(); self.addCleanup(server.server_close); self.addCleanup(server.shutdown); base=f'http://127.0.0.1:{server.server_port}'
        def req(path,headers=None,method='GET'):
            q=urllib.request.Request(base+path,headers=headers or {},method=method)
            try:
                with urllib.request.urlopen(q,timeout=3) as response:return response.code,response.headers
            except urllib.error.HTTPError as e:
                with e:return e.code,e.headers
        self.assertEqual(req('/api/search?q=Northstar')[0],403)
        good={'X-Workbench-Token':'test-session-token'}
        self.assertEqual(req('/api/health',dict(good,Host='outside.invalid'))[0],403)
        self.assertEqual(req('/api/health',dict(good,Origin='http://untrusted.invalid'))[0],403)
        self.assertEqual(req('/api/refresh',good)[0],404)
        self.assertEqual(req('/api/refresh',good,'POST')[0],403)
        self.assertEqual(req('/api/refresh',dict(good,Origin=base),'POST')[0],200)
        self.assertEqual(req('/api/search?q=Northstar',good)[0],200)
        _,asset=req('/'); self.assertIn("frame-ancestors 'none'",asset['Content-Security-Policy']); self.assertEqual(asset['Cache-Control'],'no-store')

import ast, unittest
from pathlib import Path
source=Path(__file__).parents[1].joinpath('hatch_watcher.py').read_text()
tree=ast.parse(source)
class ReleaseTests(unittest.TestCase):
 def test_older_and_equal_versions_do_not_offer_updates(self):
  fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='parse_version')
  import re
  scope={'re':re};exec(compile(ast.Module(body=[fn],type_ignores=[]),'version','exec'),scope)
  parse=scope['parse_version']
  for tag in ['v1.3.1','v2.3.4','v2.4.0','v2.5.0']: self.assertLessEqual(parse(tag),parse('2.5.0'))
  self.assertGreater(parse('2.5.1'),parse('2.5.0'))
 def test_no_preview_capture_or_thumbnail_work(self):
  app=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='App')
  for name in ['_preview_worker','on_view','_check']:
   fn=next(n for n in app.body if isinstance(n,ast.FunctionDef) and n.name==name)
   calls=[n.func.id for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
   self.assertNotIn('make_thumb',calls)
   if name!='_check': self.assertNotIn('snap_window',calls)

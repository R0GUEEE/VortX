import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("dependency_health_render", ROOT / "scripts/render-dependency-health.py")
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


class DependencyHealthRenderTests(unittest.TestCase):
    def test_all_source_records_and_counts_survive_grouping(self):
        entries = [{"ecosystem":"npm","name":"example","repository":"one","source":source,
                    "kind":"resolved-transitive","current":version,"status":status}
                   for source, version, status in (("package-lock.json","1.0.0","outdated"),
                                                   ("nested/package-lock.json","2.0.0","current"))]
        text = render.render({"entries":entries,"coverage_gaps":[{"source":"native","reason":"unknown"}],
                              "unresolved_entries":3,"complete":False})
        self.assertIn("1 distinct ecosystem/package identities; 2 declaration/lock/location records", text)
        self.assertIn("Complete: False; unresolved records: 3; coverage gaps: 1", text)
        self.assertIn("| package-lock.json | resolved-transitive | 1.0.0 | UNVERIFIED | outdated |", text)
        self.assertIn("| nested/package-lock.json | resolved-transitive | 2.0.0 | UNVERIFIED | current |", text)

    def test_untrusted_values_cannot_inject_html_or_markdown(self):
        value = '<img src="https://invalid/"> ![image](https://invalid/) | `code`\nnewline'
        escaped = render.cell(value)
        self.assertNotIn("<img", escaped)
        self.assertNotIn("![image]", escaped)
        self.assertIn("&lt;img", escaped)
        self.assertIn("\\|", escaped)
        self.assertNotIn("\n", escaped)
        text = render.render({"entries":[],"coverage_gaps":[],"checked_at":value})
        self.assertNotIn("<img", text)
        self.assertNotIn("![image]", text)


if __name__ == "__main__": unittest.main()

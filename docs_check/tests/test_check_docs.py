import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_docs

ROOT_README = "---\ntitle: Musterapp\nlayer: product\n---\n\nAnleitungen.\n"
DOCS_README = "---\ntitle: Plattform\nlayer: platform\n---\n\nAnleitungen.\n"
FOLDER_README = "---\ntitle: Buchhaltung\norder: 20\n---\n\nRechnungen.\n"
SHOTS = "shots: []\n"


def page(title="Zahlungen abgleichen", extra="", covers="[]", body="Text.\n"):
	return (
		f"---\ntitle: {title}\naudience: user\nrequires: []\ncovers: {covers}\nowner: tbd\n{extra}---\n\n{body}"
	)


def doctype_json(fields):
	return json.dumps({"name": "Sales Invoice", "modified": "2026-01-01", "fields": fields, "field_order": [f["fieldname"] for f in fields]})


class DocsTestCase(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		self.root = Path(self.tmp.name)

	def write(self, files):
		for relative, content in files.items():
			path = self.root / relative
			path.parent.mkdir(parents=True, exist_ok=True)
			path.write_text(content, encoding="utf-8")

	def valid_tree(self, **extra):
		files = {
			"client_docs/README.md": ROOT_README,
			"client_docs/shots.yaml": SHOTS,
			"client_docs/zahlungen-abgleichen.md": page(),
		}
		files.update(extra)
		return files

	def check(self, files=None, **options):
		if files is not None:
			self.write(files)
		options.setdefault("markdownlint", False)
		options.setdefault("app", "musterapp")
		return check_docs.Checker(self.root, **options).run().items

	def messages(self, files=None, **options):
		return [message for _, _, message in self.check(files, **options)]

	def assertError(self, messages, fragment):
		self.assertTrue(any(fragment in message for message in messages), f"{fragment!r} not in {messages}")


class TestSkip(DocsTestCase):
	def test_no_client_docs(self):
		self.assertEqual(self.check({"README.md": "x"}), [])

	def test_empty_client_docs(self):
		(self.root / "client_docs" / "images").mkdir(parents=True)
		(self.root / "client_docs" / "images" / ".gitkeep").write_text("")
		self.assertEqual(self.check(), [])

	def test_main_exit_code_green_for_empty(self):
		(self.root / "client_docs").mkdir()
		self.assertEqual(check_docs.main(["--root", str(self.root), "--skip-markdownlint"]), 0)


class TestValid(DocsTestCase):
	def test_valid_page(self):
		self.assertEqual(self.check(self.valid_tree()), [])

	def test_valid_topic_folder_and_links(self):
		files = self.valid_tree(
			**{
				"client_docs/buchhaltung/README.md": FOLDER_README,
				"client_docs/buchhaltung/gutschrift-erstellen.md": page(
					body="Siehe [Zahlungen](../zahlungen-abgleichen.md#abschnitt) und [Doku](https://docs.erpnext.com/x) und [Anderes](app:other_app/seite).\n"
				),
				"client_docs/shots.yaml": "shots:\n  - name: zahlung\n    route: /desk/payment-entry\n",
			}
		)
		files["client_docs/zahlungen-abgleichen.md"] = page(body="![Zahlung](images/zahlung.webp)\n\n[Eigene](app:musterapp/buchhaltung/gutschrift-erstellen)\n")
		self.assertEqual(self.check(files), [])

	def test_translation_and_variants(self):
		files = self.valid_tree(
			**{
				"client_docs/zahlungen-abgleichen.en.md": "---\ntitle: Reconcile\n---\n\nText.\n",
				"client_docs/zahlungen-abgleichen.md": page(extra="versions:\n  erpnext: '>=16.30'\n", covers="[]").replace("requires: []", "requires: [erpnext]"),
				"client_docs/zahlungen-abgleichen--bis-16-29.md": page(extra="versions: { erpnext: '<16.30' }\n").replace("requires: []", "requires: [erpnext]"),
			}
		)
		self.assertEqual(self.check(files), [])


class TestNames(DocsTestCase):
	def test_bad_page_name(self):
		self.assertError(self.messages(self.valid_tree(**{"client_docs/Zahlung_X.md": page()})), "page name")

	def test_bad_folder_name(self):
		files = self.valid_tree(**{"client_docs/Buch_haltung/README.md": FOLDER_README, "client_docs/Buch_haltung/a.md": page()})
		self.assertError(self.messages(files), "folder name")

	def test_too_deep(self):
		files = self.valid_tree(**{"client_docs/a/b/README.md": FOLDER_README, "client_docs/a/README.md": FOLDER_README, "client_docs/a/b/c.md": page()})
		self.assertError(self.messages(files), "too deep")

	def test_missing_folder_readme(self):
		files = self.valid_tree(**{"client_docs/buchhaltung/a.md": page()})
		self.assertError(self.messages(files), "needs a README.md")

	def test_bad_image_name(self):
		files = self.valid_tree(**{"client_docs/images/Bild_1.webp": "x"})
		self.assertError(self.messages(files), "image name")

	def test_unexpected_file(self):
		files = self.valid_tree(**{"client_docs/notes.txt": "x"})
		self.assertError(self.messages(files), "unexpected file")

	def test_shots_seed_allowed(self):
		files = self.valid_tree(**{"client_docs/shots_seed.py": "def run(context):\n\tpass\n"})
		self.assertEqual(self.messages(files), [])

	def test_translation_without_german(self):
		files = self.valid_tree(**{"client_docs/anderes.en.md": "---\ntitle: X\n---\n"})
		self.assertError(self.messages(files), "without German source")

	def test_vendor_folder_only_in_docs_app(self):
		files = {
			"client_docs/README.md": DOCS_README,
			"client_docs/shots.yaml": SHOTS,
			"client_docs/erpnext_germany/README.md": FOLDER_README,
			"client_docs/erpnext_germany/seite.md": page(),
		}
		self.assertEqual(self.check(files), [])
		files = self.valid_tree(**{"client_docs/erpnext_germany/README.md": FOLDER_README, "client_docs/erpnext_germany/seite.md": page()})
		self.assertError(self.messages(files), "folder name")

	def test_docs_app_page_outside_vendor_folder(self):
		files = {"client_docs/README.md": DOCS_README, "client_docs/shots.yaml": SHOTS, "client_docs/seite.md": page()}
		self.assertError(self.messages(files), "outside the vendor folders")

	def test_missing_root_readme_layer(self):
		files = self.valid_tree(**{"client_docs/README.md": "---\ntitle: X\n---\n"})
		self.assertError(self.messages(files), "'layer' is required")

	def test_layer_in_subfolder(self):
		files = self.valid_tree(**{"client_docs/b/README.md": "---\ntitle: B\nlayer: product\n---\n", "client_docs/b/a.md": page()})
		self.assertError(self.messages(files), "only allowed in client_docs/README.md")

	def test_app_without_docs(self):
		self.assertError(self.messages(self.valid_tree(), app="helpdesk"), "must not have client_docs")


class TestFrontmatter(DocsTestCase):
	def broken(self, text):
		return self.messages(self.valid_tree(**{"client_docs/zahlungen-abgleichen.md": text}))

	def test_missing_frontmatter(self):
		self.assertError(self.broken("Nur Text\n"), "missing YAML frontmatter")

	def test_invalid_yaml(self):
		self.assertError(self.broken("---\ntitle: [\n---\n"), "invalid YAML")

	def test_missing_key(self):
		self.assertError(self.broken("---\ntitle: X\naudience: user\nrequires: []\ncovers: []\n---\n"), "missing required frontmatter key 'owner'")

	def test_unknown_key(self):
		self.assertError(self.broken(page(extra="foo: 1\n")), "unknown key 'foo'")

	def test_bad_audience(self):
		self.assertError(self.broken(page().replace("audience: user", "audience: all")), "'audience'")

	def test_bad_covers(self):
		self.assertError(self.broken(page(covers="['doctype: Sales Invoice']")), "invalid covers entry")
		self.assertError(self.broken(page(covers="['table:Foo']")), "invalid covers entry")

	def test_bad_order(self):
		self.assertError(self.broken(page(extra="order: high\n")), "'order' must be an integer")

	def test_requires_lists_own_app(self):
		self.assertError(self.broken(page().replace("requires: []", "requires: [musterapp]")), "own app")

	def test_versions_key_rule(self):
		self.assertError(self.broken(page(extra="versions: { erpnext: '>=16' }\n")), "must be the page's own app")

	def test_versions_bad_specifier(self):
		self.assertError(self.broken(page(extra="versions: { frappe: 'neu' }\n")), "PEP 440")

	def test_variant_group_needs_versions(self):
		files = self.valid_tree(**{"client_docs/zahlungen-abgleichen--alt.md": page(extra="versions: { frappe: '<16' }\n")})
		self.assertError(self.messages(files), "needs a 'versions' key")

	def test_variant_without_base(self):
		files = self.valid_tree(**{"client_docs/anderes--alt.md": page(extra="versions: { frappe: '<16' }\n")})
		self.assertError(self.messages(files), "without base page")

	def test_translation_extra_key(self):
		files = self.valid_tree(**{"client_docs/zahlungen-abgleichen.en.md": "---\ntitle: X\naudience: user\n---\n"})
		self.assertError(self.messages(files), "unknown key 'audience'")

	def test_translation_needs_title(self):
		files = self.valid_tree(**{"client_docs/zahlungen-abgleichen.en.md": "---\n{}\n---\n"})
		self.assertError(self.messages(files), "'title' is required")

	def test_line_number_points_at_key(self):
		items = self.check(self.valid_tree(**{"client_docs/zahlungen-abgleichen.md": page(extra="order: high\n")}))
		self.assertIn(("client_docs/zahlungen-abgleichen.md", 7, "'order' must be an integer"), items)

	def test_h1_and_deep_heading(self):
		messages = self.broken(page(body="# Titel\n\n#### Tief\n\n```\n# in code\n```\n"))
		self.assertError(messages, "no '#' heading")
		self.assertError(messages, "no deeper headings")
		self.assertEqual(len(messages), 2)


class TestLinks(DocsTestCase):
	def links(self, body, **extra):
		files = self.valid_tree(**extra)
		files["client_docs/zahlungen-abgleichen.md"] = page(body=body)
		return self.messages(files)

	def test_broken_relative_link(self):
		self.assertError(self.links("[X](fehlt.md)\n"), "link target does not exist")

	def test_link_without_md(self):
		self.assertError(self.links("[X](seite)\n"), "must point to a .md page")

	def test_http_link(self):
		self.assertError(self.links("[X](http://example.com)\n"), "https://")

	def test_mailto_link(self):
		self.assertError(self.links("[X](mailto:a@b.ch)\n"), "https://")

	def test_link_in_code_ignored(self):
		self.assertEqual(self.links("`[X](fehlt.md)`\n\n```\n[X](fehlt.md)\n```\n"), [])

	def test_link_to_variant_or_translation(self):
		messages = self.links("[X](a.en.md)\n", **{"client_docs/a.md": page(), "client_docs/a.en.md": "---\ntitle: A\n---\n"})
		self.assertError(messages, "German base page")

	def test_link_to_readme(self):
		self.assertError(self.links("[X](README.md)\n"), "not a page")

	def test_link_leaves_docs(self):
		self.assertError(self.links("[X](../README.md)\n"), "leaves client_docs")

	def test_app_link_syntax(self):
		self.assertError(self.links("[X](app:Foo/bar)\n"), "invalid app: link")
		self.assertError(self.links("[X](app:foo/bar.md)\n"), "invalid app: link")

	def test_same_repo_app_link_target_must_exist(self):
		self.assertError(self.links("[X](app:musterapp/fehlt)\n"), "app: link target does not exist")

	def test_other_app_link_not_resolved(self):
		self.assertEqual(self.links("[X](app:fremdapp/fehlt)\n"), [])

	def test_image_missing_without_shot(self):
		self.assertError(self.links("![Bild](images/fehlt.webp)\n"), "no entry in shots.yaml")

	def test_image_exists(self):
		self.assertEqual(self.links("![Bild](images/da.webp)\n", **{"client_docs/images/da.webp": "x"}), [])

	def test_image_without_alt(self):
		self.assertError(self.links("![](images/da.webp)\n", **{"client_docs/images/da.webp": "x"}), "alt text")

	def test_image_not_webp(self):
		self.assertError(self.links("![Bild](images/da.png)\n", **{"client_docs/images/da.png": "x"}), "WebP")

	def test_external_image(self):
		self.assertError(self.links("![Bild](https://example.com/a.webp)\n"), "external URLs")

	def test_docs_app_link_must_stay_in_vendor_folder(self):
		files = {
			"client_docs/README.md": DOCS_README,
			"client_docs/shots.yaml": SHOTS,
			"client_docs/erpnext/README.md": FOLDER_README,
			"client_docs/erpnext/a.md": page(body="[X](../hrms/b.md)\n"),
			"client_docs/hrms/README.md": FOLDER_README,
			"client_docs/hrms/b.md": page(),
		}
		self.assertError(self.messages(files), "stay in the vendor folder")

	def test_docs_app_app_link_same_repo(self):
		files = {
			"client_docs/README.md": DOCS_README,
			"client_docs/shots.yaml": SHOTS,
			"client_docs/erpnext/README.md": FOLDER_README,
			"client_docs/erpnext/a.md": page(body="[X](app:hrms/fehlt)\n"),
			"client_docs/hrms/README.md": FOLDER_README,
			"client_docs/hrms/b.md": page(),
		}
		self.assertError(self.messages(files), "app: link target does not exist")


class TestShots(DocsTestCase):
	def shots(self, text):
		return self.messages(self.valid_tree(**{"client_docs/shots.yaml": text}))

	def test_missing_file(self):
		files = self.valid_tree()
		self.write(files)
		(self.root / "client_docs" / "shots.yaml").unlink()
		self.assertError(self.messages(), "missing client_docs/shots.yaml")

	def test_complete_example(self):
		text = (
			"shots:\n  - name: erpnext-zahlungsabgleich\n    route: /desk/payment-reconciliation\n    wait: .form-layout\n    settle: 1500\n    user: manager\n"
			"    steps:\n      - fill: { field: party_type, value: Customer }\n      - click: Get Unreconciled Entries\n      - scrollTo: { field: allocation }\n"
			"      - highlight: { label: Reconcile }\n      - dialog: { selector: .btn }\n    hideText:\n      - '^Zuletzt.*$'\n    crop: { selector: .layout-main-section }\n"
		)
		self.assertEqual(self.shots(text), [])

	def test_wrong_root(self):
		self.assertError(self.shots("foo: []\n"), "the key 'shots'")

	def test_profile(self):
		self.assertEqual(self.shots("profile: full\nshots: []\n"), [])
		self.assertError(self.shots("profile: clean\nshots: []\n"), "'profile' must be one of")

	def test_docs_app_needs_base_profile(self):
		shot = "shots:\n  - name: erpnext-angebot\n    route: /desk/quotation\n"
		files = {"client_docs/README.md": DOCS_README, "client_docs/erpnext/README.md": FOLDER_README, "client_docs/erpnext/seite.md": page()}
		self.assertError(self.messages({**files, "client_docs/shots.yaml": shot}), "need 'profile: base'")
		self.assertEqual(self.check({**files, "client_docs/shots.yaml": "profile: base\n" + shot}), [])

	def test_unknown_key(self):
		self.assertError(self.shots("shots:\n  - name: a\n    route: /x\n    color: red\n"), "unknown key 'color'")

	def test_missing_route(self):
		self.assertError(self.shots("shots:\n  - name: a\n"), "missing required key 'route'")

	def test_duplicate_names(self):
		self.assertError(self.shots("shots:\n  - name: a\n    route: /x\n  - name: a\n    route: /y\n"), "duplicate shot name")

	def test_bad_user(self):
		self.assertError(self.shots("shots:\n  - name: a\n    route: /x\n    user: root\n"), "'user' must be")

	def test_bad_step(self):
		self.assertError(self.shots("shots:\n  - name: a\n    route: /x\n    steps:\n      - hover: x\n"), "exactly one of")

	def test_fill_without_value(self):
		self.assertError(self.shots("shots:\n  - name: a\n    route: /x\n    steps:\n      - fill: { field: x }\n"), "plus 'value'")

	def test_bad_target(self):
		self.assertError(self.shots("shots:\n  - name: a\n    route: /x\n    steps:\n      - click: { label: A, field: b }\n"), "exactly one non-empty key")

	def test_bad_hide_text(self):
		self.assertError(self.shots("shots:\n  - name: a\n    route: /x\n    hideText: ['(']\n"), "invalid regular expression")

	def test_docs_app_shot_prefix(self):
		files = {
			"client_docs/README.md": DOCS_README,
			"client_docs/shots.yaml": "shots:\n  - name: zahlung\n    route: /x\n",
			"client_docs/erpnext/README.md": FOLDER_README,
			"client_docs/erpnext/a.md": page(),
		}
		self.assertError(self.messages(files), "vendor folder name")


class TestMarkdownlintOutput(DocsTestCase):
	def test_parses_output(self):
		result = subprocess.CompletedProcess([], 1, stdout="", stderr="client_docs/a.md:9:1 error MD025/single-title Multiple top-level headings\n")
		checker = check_docs.Checker(self.root)
		checker.report_markdownlint(result)
		self.assertEqual(checker.findings.items[0][:2], ("client_docs/a.md", 9))

	def test_tool_failure_is_error(self):
		result = subprocess.CompletedProcess([], 2, stdout="", stderr="boom\n")
		checker = check_docs.Checker(self.root)
		checker.report_markdownlint(result)
		self.assertIn("failed to run", checker.findings.items[0][2])


class TestCovers(DocsTestCase):
	def git(self, *args):
		subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

	def commit(self, message):
		self.git("add", "-A")
		self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", message)
		return subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

	def base_tree(self, covers="['doctype:Sales Invoice']"):
		self.git("init", "-q")
		files = self.valid_tree(**{"client_docs/zahlungen-abgleichen.md": page(covers=covers)})
		files["musterapp/musterapp/doctype/sales_invoice/sales_invoice.json"] = doctype_json([{"fieldname": "a", "label": "A"}])
		self.write(files)
		return self.commit("base")

	def covers(self, base, head, **options):
		return self.messages(base=base, head=head, **options)

	def test_covered_change_without_page_fails(self):
		base = self.base_tree()
		self.write({"musterapp/musterapp/doctype/sales_invoice/sales_invoice.json": doctype_json([{"fieldname": "a", "label": "A"}, {"fieldname": "b", "label": "B"}])})
		messages = self.covers(base, self.commit("change"))
		self.assertError(messages, "covered item 'doctype:Sales Invoice' changed")
		self.assertError(messages, "update the page")

	def test_failure_names_the_page(self):
		base = self.base_tree()
		self.write({"musterapp/musterapp/doctype/sales_invoice/sales_invoice.json": doctype_json([{"fieldname": "b"}])})
		items = self.check(base=base, head=self.commit("change"))
		self.assertEqual(items[0][0], "client_docs/zahlungen-abgleichen.md")

	def test_covered_change_with_page_change_passes(self):
		base = self.base_tree()
		self.write(
			{
				"musterapp/musterapp/doctype/sales_invoice/sales_invoice.json": doctype_json([{"fieldname": "b"}]),
				"client_docs/zahlungen-abgleichen.md": page(covers="['doctype:Sales Invoice']", body="Neu.\n"),
			}
		)
		self.assertEqual(self.covers(base, self.commit("change")), [])

	def test_translation_change_counts_as_page_update(self):
		base = self.base_tree()
		self.write(
			{
				"musterapp/musterapp/doctype/sales_invoice/sales_invoice.json": doctype_json([{"fieldname": "b"}]),
				"client_docs/zahlungen-abgleichen.en.md": "---\ntitle: Reconcile\n---\n",
			}
		)
		self.assertEqual(self.covers(base, self.commit("change")), [])

	def test_modified_alone_never_counts(self):
		base = self.base_tree()
		text = doctype_json([{"fieldname": "a", "label": "A"}]).replace("2026-01-01", "2026-02-02")
		self.write({"musterapp/musterapp/doctype/sales_invoice/sales_invoice.json": text})
		self.assertEqual(self.covers(base, self.commit("touch")), [])

	def test_other_doctype_key_change_does_not_count(self):
		base = self.base_tree()
		data = json.loads(doctype_json([{"fieldname": "a", "label": "A"}]))
		data["description"] = "neu"
		self.write({"musterapp/musterapp/doctype/sales_invoice/sales_invoice.json": json.dumps(data)})
		self.assertEqual(self.covers(base, self.commit("desc")), [])

	def test_uncovered_change_never_fails(self):
		base = self.base_tree()
		self.write({"musterapp/musterapp/doctype/other/other.json": doctype_json([{"fieldname": "x"}])})
		self.assertEqual(self.covers(base, self.commit("other")), [])

	def test_no_docs_label_skips(self):
		base = self.base_tree()
		self.write({"musterapp/musterapp/doctype/sales_invoice/sales_invoice.json": doctype_json([{"fieldname": "b"}])})
		self.assertEqual(self.covers(base, self.commit("change"), labels=["no-docs"]), [])

	def test_skipped_in_docs_app(self):
		self.git("init", "-q")
		files = {
			"client_docs/README.md": DOCS_README,
			"client_docs/shots.yaml": SHOTS,
			"client_docs/erpnext/README.md": FOLDER_README,
			"client_docs/erpnext/a.md": page(covers="['js:x/a.js']"),
			"x/a.js": "1",
		}
		self.write(files)
		base = self.commit("base")
		self.write({"x/a.js": "2"})
		self.assertEqual(self.covers(base, self.commit("change")), [])

	def test_custom_field_fixture_entry(self):
		self.git("init", "-q")
		fixture = lambda label: json.dumps([{"dt": "Sales Invoice", "fieldname": "x", "label": label, "modified": "1"}, {"dt": "Customer", "fieldname": "y"}])
		self.write(self.valid_tree(**{"client_docs/zahlungen-abgleichen.md": page(covers="['doctype:Sales Invoice']"), "musterapp/fixtures/custom_field.json": fixture("A")}))
		base = self.commit("base")
		self.write({"musterapp/fixtures/custom_field.json": fixture("B")})
		self.assertError(self.covers(base, self.commit("change")), "custom_field.json")

	def test_other_types(self):
		covers = "['report:General Ledger', 'print_format:Rechnung QR', 'page:pain-export-tool', 'workspace:Buchhaltung', 'www:time_portal', 'js:musterapp/public/js/customer.js', 'file:frontend/src/pages/Leave*.vue']"
		cases = {
			"musterapp/musterapp/report/general_ledger/general_ledger.py": "report:General Ledger",
			"musterapp/musterapp/print_format/rechnung_qr/rechnung_qr.html": "print_format:Rechnung QR",
			"musterapp/musterapp/page/pain_export_tool/pain_export_tool.js": "page:pain-export-tool",
			"musterapp/musterapp/workspace/buchhaltung/buchhaltung.json": "workspace:Buchhaltung",
			"musterapp/www/time_portal.html": "www:time_portal",
			"musterapp/www/time_portal/index.py": "www:time_portal",
			"musterapp/public/js/customer.js": "js:musterapp/public/js/customer.js",
			"frontend/src/pages/LeaveList.vue": "file:frontend/src/pages/Leave*.vue",
		}
		self.git("init", "-q")
		self.write(self.valid_tree(**{"client_docs/zahlungen-abgleichen.md": page(covers=covers)}))
		base = self.commit("base")
		for path, entry in cases.items():
			self.write({path: "{}" if path.endswith(".json") else "x"})
			head = self.commit(path)
			self.assertError(self.covers(base, head), f"covered item '{entry}'")
			base = head

	def test_not_matching_paths(self):
		covers = "['www:time_portal', 'file:frontend/src/pages/Leave*.vue', 'report:General Ledger']"
		self.git("init", "-q")
		self.write(self.valid_tree(**{"client_docs/zahlungen-abgleichen.md": page(covers=covers)}))
		base = self.commit("base")
		self.write({"musterapp/www/time_portal_x.html": "x", "frontend/src/pages/sub/LeaveList.vue": "x", "musterapp/musterapp/report/general_ledger_x/a.py": "x"})
		self.assertEqual(self.covers(base, self.commit("other")), [])


class TestOutput(unittest.TestCase):
	def test_github_annotation(self):
		self.assertEqual(check_docs.format_finding("a.md", 3, "bad\nthing", True), "::error file=a.md,line=3::bad%0Athing")

	def test_plain(self):
		self.assertEqual(check_docs.format_finding("a.md", 3, "bad", False), "a.md:3: bad")

	def test_labels(self):
		self.assertEqual(check_docs.parse_labels('["no-docs", "x"]'), ["no-docs", "x"])
		self.assertEqual(check_docs.parse_labels(""), [])


if __name__ == "__main__":
	unittest.main()

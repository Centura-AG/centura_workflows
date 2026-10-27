#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

MARKDOWNLINT_CLI2_VERSION = "0.18.1"

PAGE_RE = re.compile(
	r"^(?P<base>[a-z0-9]+(?:-[a-z0-9]+)*)(?:--(?P<suffix>[a-z0-9]+(?:-[a-z0-9]+)*))?(?:\.(?P<lang>en|fr))?\.md$"
)
FOLDER_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
VENDOR_FOLDER_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
IMAGE_RE = re.compile(r"^(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)(?:\.(?:en|fr))?\.webp$")
SHOT_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
APP_LINK_RE = re.compile(r"^app:[a-z0-9_]+(/[a-z0-9]+(-[a-z0-9]+)*)+$")
APP_NAME_RE = re.compile(r"^[a-z0-9_]+$")
OWNER_RE = re.compile(r"^(tbd|[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))$")
COVERS_RE = re.compile(r"^(doctype|report|print_format|page|workspace|www|js|file):(\S.*)$")
SPECIFIER_RE = re.compile(
	r"^\s*(===|==|!=|<=|>=|~=|<|>)\s*\d+(\.(\d+|\*))*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?\s*"
	r"(,\s*(===|==|!=|<=|>=|~=|<|>)\s*\d+(\.(\d+|\*))*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?\s*)*$"
)
INLINE_LINK_RE = re.compile(
	r"(!?)\[((?:[^\]\\]|\\.)*)\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
REFERENCE_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+.*)?$")
AUTOLINK_RE = re.compile(r"<([a-zA-Z][a-zA-Z0-9+.-]*:[^>\s]+)>")
BARE_HTTP_RE = re.compile(r"(?<![\w/:\"(<])http://[^\s)>\]]+")
HEADING_RE = re.compile(r"^(#{1,6})(?:\s|$)")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")

AUDIENCES = ("user", "admin", "internal")
LAYERS = ("customer", "product", "platform")
SHOT_PROFILES = ("base", "full")
PAGE_KEYS = ("title", "audience", "requires", "covers", "owner", "roles", "versions", "order")
README_KEYS = ("title", "order", "layer")
SHOT_KEYS = ("name", "route", "user", "wait", "settle", "steps", "hideText", "crop", "css")
SHOT_USERS = ("user", "manager", "guest")
TARGET_KEYS = ("label", "field", "selector")
TARGET_STEPS = ("click", "dialog", "scrollTo", "highlight")
STEPS = TARGET_STEPS + ("fill",)
APPS_WITHOUT_DOCS = (
	"raven",
	"helpdesk",
	"telephony",
	"centura_ai",
	"opex",
	"centura_flow",
	"drive",
	"print_designer",
)


class LDict(dict):
	line = 1
	key_lines = None
	duplicates = None


class LineLoader(yaml.SafeLoader):
	line_offset = 0


def construct_mapping(loader, node):
	mapping = LDict()
	mapping.line = node.start_mark.line + 1 + loader.line_offset
	mapping.key_lines = {}
	mapping.duplicates = []
	for key_node, value_node in node.value:
		key = loader.construct_object(key_node, deep=True)
		value = loader.construct_object(value_node, deep=True)
		line = key_node.start_mark.line + 1 + loader.line_offset
		try:
			if key in mapping:
				mapping.duplicates.append((key, line))
			mapping[key] = value
			mapping.key_lines[key] = line
		except TypeError:
			mapping.duplicates.append((str(key), line))
	return mapping


LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)


def parse_yaml(text, line_offset=0):
	loader = LineLoader(text)
	loader.line_offset = line_offset
	try:
		return loader.get_single_data(), None
	except yaml.YAMLError as error:
		mark = getattr(error, "problem_mark", None)
		line = mark.line + 1 + line_offset if mark else 1 + line_offset
		problem = getattr(error, "problem", None) or str(error)
		return None, (line, f"invalid YAML: {problem}")
	finally:
		loader.dispose()


def split_frontmatter(text):
	lines = text.split("\n")
	if not lines or lines[0].rstrip() != "---":
		return None
	for index in range(1, len(lines)):
		if lines[index].rstrip() == "---":
			return "\n".join(lines[1:index]), index + 1
	return None


def scrub(value):
	return value.replace(" ", "_").replace("-", "_").lower()


def is_text(value):
	return isinstance(value, str) and bool(value.strip())


def is_integer(value):
	return isinstance(value, int) and not isinstance(value, bool)


def glob_to_regex(glob):
	out = []
	index = 0
	while index < len(glob):
		if glob.startswith("**/", index):
			out.append("(?:.*/)?")
			index += 3
		elif glob.startswith("**", index):
			out.append(".*")
			index += 2
		elif glob[index] == "*":
			out.append("[^/]*")
			index += 1
		elif glob[index] == "?":
			out.append("[^/]")
			index += 1
		else:
			out.append(re.escape(glob[index]))
			index += 1
	return re.compile("".join(out))


def strip_modified(value):
	if isinstance(value, dict):
		return {key: strip_modified(item) for key, item in value.items() if key != "modified"}
	if isinstance(value, list):
		return [strip_modified(item) for item in value]
	return value


def detect_app(root):
	for child in sorted(Path(root).iterdir()):
		if child.is_dir() and (child / "hooks.py").is_file():
			return child.name
	repository = os.environ.get("GITHUB_REPOSITORY", "")
	name = repository.split("/")[-1] if repository else Path(root).resolve().name
	return name.replace("-", "_")


class Findings:
	def __init__(self):
		self.items = []

	def add(self, file, line, message):
		self.items.append((file, line, message))

	def __len__(self):
		return len(self.items)


class Page:
	def __init__(self, docs, path):
		self.path = path
		self.rel_docs = path.relative_to(docs.docs)
		self.rel = path.relative_to(docs.root).as_posix()
		self.parts = self.rel_docs.parts
		match = PAGE_RE.match(path.name)
		self.base = match.group("base") if match else None
		self.suffix = match.group("suffix") if match else None
		self.lang = match.group("lang") if match else None
		self.vendor = self.parts[0] if docs.is_docs_app and len(self.parts) > 1 else None
		self.app = self.vendor or docs.app
		self.folder = self.rel_docs.parent.as_posix()
		self.data = None
		self.text = ""
		self.body_start = 1

	@property
	def group(self):
		return (self.folder, self.base)

	@property
	def german_name(self):
		if not self.base:
			return None
		suffix = f"--{self.suffix}" if self.suffix else ""
		return f"{self.base}{suffix}.md"


class Checker:
	def __init__(self, root, app=None, base=None, head=None, labels=(), markdownlint=True, markdownlint_version=MARKDOWNLINT_CLI2_VERSION):
		self.root = Path(root).resolve()
		self.docs = self.root / "client_docs"
		self.app = app or detect_app(self.root)
		self.base = base
		self.head = head
		self.labels = list(labels)
		self.markdownlint = markdownlint
		self.markdownlint_version = markdownlint_version
		self.findings = Findings()
		self.is_docs_app = False
		self.pages = []
		self.shot_names = set()

	def error(self, file, line, message):
		if isinstance(file, Path):
			file = file.relative_to(self.root).as_posix()
		self.findings.add(file, line, message)

	def visible_files(self):
		if not self.docs.is_dir():
			return []
		files = []
		for path in sorted(self.docs.rglob("*")):
			relative = path.relative_to(self.docs)
			if path.is_file() and not any(part.startswith(".") for part in relative.parts):
				files.append(path)
		return files

	def run(self):
		files = self.visible_files()
		if not files:
			return self.findings
		self.read_root_readme()
		self.check_app_allowed()
		self.check_structure(files)
		self.pages = self.collect_pages(files)
		self.check_readmes(files)
		self.check_shots()
		self.check_page_groups()
		for page in self.pages:
			self.check_page(page)
		if self.markdownlint:
			self.run_markdownlint()
		self.check_covers()
		return self.findings

	def read_root_readme(self):
		readme = self.docs / "README.md"
		self.is_docs_app = False
		if not readme.is_file():
			return
		parsed = self.read_frontmatter(readme)
		if parsed and isinstance(parsed[0], dict):
			self.is_docs_app = parsed[0].get("layer") == "platform"

	def read_frontmatter(self, path, report=False):
		text = path.read_text(encoding="utf-8", errors="replace")
		split = split_frontmatter(text)
		if split is None:
			if report:
				self.error(path, 1, "missing YAML frontmatter between two --- lines")
			return None
		data, problem = parse_yaml(split[0], 1)
		if problem:
			if report:
				self.error(path, problem[0], problem[1])
			return None
		if not isinstance(data, dict):
			if report:
				self.error(path, 2, "frontmatter must be a YAML map")
			return None
		return data, split[1], text

	def check_app_allowed(self):
		if self.app in APPS_WITHOUT_DOCS:
			self.error(self.docs / "README.md", 1, f"app '{self.app}' must not have client_docs/ (CONVENTIONS section 3)")

	def check_structure(self, files):
		max_folders = 2 if self.is_docs_app else 1
		seen_folders = set()
		for path in files:
			relative = path.relative_to(self.docs)
			parts = relative.parts
			if parts[0] == "images":
				self.check_image_file(path, parts)
				continue
			for depth, folder in enumerate(parts[:-1]):
				key = parts[: depth + 1]
				if key in seen_folders:
					continue
				seen_folders.add(key)
				self.check_folder_name(path, folder, depth)
			name = parts[-1]
			if len(parts) == 1 and name in ("README.md", "shots.yaml", "shots_seed.py"):
				continue
			if not name.endswith(".md"):
				self.error(path, 1, f"unexpected file '{name}': only pages, README.md, shots.yaml, shots_seed.py and images/ are allowed")
				continue
			if name == "README.md":
				continue
			self.check_page_location(path, parts, max_folders)

	def check_image_file(self, path, parts):
		if len(parts) > 2:
			self.error(path, 1, "images/ is flat: no sub-folders")
		elif not IMAGE_RE.match(parts[-1]):
			self.error(path, 1, f"image name '{parts[-1]}' must match {IMAGE_RE.pattern}")

	def check_folder_name(self, path, folder, depth):
		if self.is_docs_app and depth == 0:
			if not VENDOR_FOLDER_RE.match(folder):
				self.error(path, 1, f"vendor folder '{folder}' must be the app name (lower case, underscores)")
		elif not FOLDER_RE.match(folder):
			self.error(path, 1, f"folder name '{folder}' must match {FOLDER_RE.pattern}")

	def check_page_location(self, path, parts, max_folders):
		name = parts[-1]
		if not PAGE_RE.match(name):
			self.error(path, 1, f"page name '{name}' must match {PAGE_RE.pattern}")
		folders = len(parts) - 1
		if self.is_docs_app and folders == 0:
			self.error(path, 1, "centura_docs has no pages outside the vendor folders")
		elif folders > max_folders:
			self.error(path, 1, f"too deep: at most {max_folders} folder level(s) below client_docs/")

	def collect_pages(self, files):
		pages = []
		for path in files:
			parts = path.relative_to(self.docs).parts
			if parts[0] == "images" or not path.name.endswith(".md") or path.name == "README.md":
				continue
			pages.append(Page(self, path))
		return pages

	def check_readmes(self, files):
		folders = {Path(".")}
		for page in self.pages:
			folders.add(page.rel_docs.parent)
			if self.is_docs_app and len(page.parts) > 1:
				folders.add(Path(page.parts[0]))
		for folder in sorted(folders):
			readme = self.docs / folder / "README.md"
			if not readme.is_file():
				location = readme.parent if readme.parent.is_dir() else self.docs
				self.error(location, 1, "folder with pages needs a README.md (folder index)")
				continue
			self.check_readme(readme, folder == Path("."))

	def check_readme(self, readme, is_root):
		parsed = self.read_frontmatter(readme, report=True)
		if parsed is None:
			return
		data = parsed[0]
		self.report_unknown_keys(readme, data, README_KEYS)
		self.report_duplicates(readme, data)
		if not is_text(data.get("title")):
			self.error(readme, data.key_lines.get("title", 2), "'title' is required and must be a non-empty string")
		if "order" in data and not is_integer(data["order"]):
			self.error(readme, data.key_lines["order"], "'order' must be an integer")
		self.check_readme_layer(readme, data, is_root)

	def check_readme_layer(self, readme, data, is_root):
		if is_root and "layer" not in data:
			self.error(readme, 2, "'layer' is required in client_docs/README.md")
		elif "layer" in data and not is_root:
			self.error(readme, data.key_lines["layer"], "'layer' is only allowed in client_docs/README.md")
		elif "layer" in data and data["layer"] not in LAYERS:
			self.error(readme, data.key_lines["layer"], f"'layer' must be one of {', '.join(LAYERS)}")

	def report_unknown_keys(self, path, data, allowed):
		for key in data:
			if key not in allowed:
				self.error(path, data.key_lines.get(key, 2), f"unknown key '{key}'")

	def report_duplicates(self, path, data):
		for key, line in data.duplicates:
			self.error(path, line, f"duplicate key '{key}'")

	def check_page_groups(self):
		groups = {}
		for page in self.pages:
			if page.base:
				groups.setdefault(page.group, []).append(page)
		for (folder, base), members in groups.items():
			names = {page.path.name for page in members}
			for page in members:
				self.check_group_member(page, names)

	def check_group_member(self, page, names):
		if page.lang and page.german_name not in names:
			self.error(page.path, 1, f"translation without German source '{page.german_name}'")
		elif page.suffix and f"{page.base}.md" not in names:
			self.error(page.path, 1, f"version variant without base page '{page.base}.md'")

	def has_variants(self, page):
		return any(
			other.suffix and other.group == page.group for other in self.pages if other.base
		)

	def check_page(self, page):
		parsed = self.read_frontmatter(page.path, report=True)
		page.text = page.path.read_text(encoding="utf-8", errors="replace")
		if parsed is None:
			return
		data, body_line, text = parsed
		page.data, page.body_start = data, body_line + 1
		self.report_duplicates(page.path, data)
		if page.lang:
			self.check_translation_frontmatter(page, data)
		else:
			self.check_german_frontmatter(page, data)
		self.check_body(page)

	def check_translation_frontmatter(self, page, data):
		self.report_unknown_keys(page.path, data, ("title",))
		if not is_text(data.get("title")):
			self.error(page.path, 2, "'title' is required in a translation")

	def check_german_frontmatter(self, page, data):
		self.report_unknown_keys(page.path, data, PAGE_KEYS)
		for key in ("title", "audience", "requires", "covers", "owner"):
			if key not in data:
				self.error(page.path, 2, f"missing required frontmatter key '{key}'")
		self.check_scalar_keys(page, data)
		self.check_requires(page, data)
		self.check_covers_syntax(page, data)
		self.check_roles(page, data)
		self.check_versions(page, data)

	def check_scalar_keys(self, page, data):
		line = data.key_lines.get
		if "title" in data and not is_text(data["title"]):
			self.error(page.path, line("title"), "'title' must be a non-empty string")
		if "audience" in data and data["audience"] not in AUDIENCES:
			self.error(page.path, line("audience"), f"'audience' must be one of {', '.join(AUDIENCES)}")
		if "owner" in data and not (isinstance(data["owner"], str) and OWNER_RE.match(data["owner"])):
			self.error(page.path, line("owner"), "'owner' must be a GitHub login or 'tbd'")
		if "order" in data and not is_integer(data["order"]):
			self.error(page.path, line("order"), "'order' must be an integer")

	def check_requires(self, page, data):
		if "requires" not in data:
			return
		line = data.key_lines["requires"]
		requires = data["requires"]
		if not isinstance(requires, list) or not all(isinstance(item, str) and APP_NAME_RE.match(item) for item in requires):
			self.error(page.path, line, "'requires' must be a list of app names")
			return
		if page.app in requires:
			self.error(page.path, line, f"'requires' must not list the page's own app '{page.app}'")

	def check_covers_syntax(self, page, data):
		if "covers" not in data:
			return
		line = data.key_lines["covers"]
		covers = data["covers"]
		if not isinstance(covers, list):
			self.error(page.path, line, "'covers' must be a list")
			return
		for entry in covers:
			if not (isinstance(entry, str) and COVERS_RE.match(entry)):
				self.error(page.path, line, f"invalid covers entry {entry!r}: use '<type>:<value>' with type doctype, report, print_format, page, workspace, www, js or file")

	def check_roles(self, page, data):
		if "roles" not in data:
			return
		roles = data["roles"]
		if not isinstance(roles, list) or not all(is_text(role) for role in roles):
			self.error(page.path, data.key_lines["roles"], "'roles' must be a list of role names")

	def check_versions(self, page, data):
		versions = data.get("versions")
		if "versions" not in data:
			if page.base and self.has_variants(page):
				self.error(page.path, 2, "every variant of a page with version variants needs a 'versions' key")
			return
		line = data.key_lines["versions"]
		if not isinstance(versions, dict):
			self.error(page.path, line, "'versions' must be a map of app name to version specifier")
			return
		requires = data.get("requires") if isinstance(data.get("requires"), list) else []
		allowed = {page.app, "frappe", *requires}
		for app, specifier in versions.items():
			if app not in allowed:
				self.error(page.path, line, f"'versions' key '{app}' must be the page's own app, an app in 'requires' or frappe")
			if not (isinstance(specifier, str) and SPECIFIER_RE.match(specifier)):
				self.error(page.path, line, f"'versions' value for '{app}' must be a PEP 440 specifier, got {specifier!r}")

	def body_lines(self, page):
		lines = page.text.split("\n")
		in_fence = None
		for number in range(page.body_start, len(lines) + 1):
			line = lines[number - 1]
			fence = FENCE_RE.match(line)
			if fence:
				marker = fence.group(1)[0]
				if in_fence is None:
					in_fence = marker
				elif in_fence == marker:
					in_fence = None
				continue
			if in_fence is None:
				yield number, INLINE_CODE_RE.sub(lambda match: " " * len(match.group(0)), line)

	def check_body(self, page):
		for number, line in self.body_lines(page):
			self.check_heading(page, number, line)
			self.check_line_links(page, number, line)

	def check_heading(self, page, number, line):
		match = HEADING_RE.match(line)
		if not match:
			return
		level = len(match.group(1))
		if level == 1:
			self.error(page.path, number, "no '#' heading in the body: the viewer shows 'title' as the page heading")
		elif level > 3:
			self.error(page.path, number, "use '##' and '###' only, no deeper headings")

	def check_line_links(self, page, number, line):
		for match in INLINE_LINK_RE.finditer(line):
			is_image, text, href = match.group(1) == "!", match.group(2), match.group(3)
			if is_image and not text.strip():
				self.error(page.path, number, "image needs an alt text")
			self.check_href(page, number, href, is_image)
		reference = REFERENCE_RE.match(line)
		if reference:
			self.check_href(page, number, reference.group(1), False)
		for match in AUTOLINK_RE.finditer(line):
			self.check_href(page, number, match.group(1), False)
		for match in BARE_HTTP_RE.finditer(line):
			self.error(page.path, number, f"use https:// links only: {match.group(0)}")

	def check_href(self, page, number, href, is_image):
		if href.startswith("#"):
			return
		if href.startswith("app:"):
			self.check_app_link(page, number, href, is_image)
		elif href.startswith("https://"):
			if is_image:
				self.error(page.path, number, "images must be local files in client_docs/images/, not external URLs")
		elif re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href):
			self.error(page.path, number, f"use https:// links only: {href}")
		elif href.startswith("/"):
			self.error(page.path, number, f"use a relative link, not an absolute path: {href}")
		elif is_image:
			self.check_image_link(page, number, href)
		else:
			self.check_page_link(page, number, href)

	def check_app_link(self, page, number, href, is_image):
		if is_image:
			self.error(page.path, number, "images cannot use app: links")
			return
		if not APP_LINK_RE.match(href):
			self.error(page.path, number, f"invalid app: link '{href}': use app:<app>/<page-path> without .md")
			return
		app, _, target = href[len("app:"):].partition("/")
		vendors = {path.name for path in self.docs.iterdir() if path.is_dir() and path.name != "images"}
		if self.is_docs_app and app in vendors:
			expected = self.docs / app / f"{target}.md"
		elif not self.is_docs_app and app == self.app:
			expected = self.docs / f"{target}.md"
		else:
			return
		if not expected.is_file():
			self.error(page.path, number, f"app: link target does not exist: {expected.relative_to(self.root).as_posix()}")

	def resolve(self, page, href):
		target = href.split("#", 1)[0].split("?", 1)[0]
		resolved = Path(os.path.normpath(page.path.parent / target))
		try:
			resolved.relative_to(self.docs)
		except ValueError:
			return None, target
		return resolved, target

	def check_page_link(self, page, number, href):
		resolved, target = self.resolve(page, href)
		if resolved is None:
			self.error(page.path, number, f"link leaves client_docs/: {href}")
			return
		if not target.endswith(".md"):
			self.error(page.path, number, f"link must point to a .md page: {href}")
			return
		if self.is_docs_app and page.vendor and resolved.relative_to(self.docs).parts[0] != page.vendor:
			self.error(page.path, number, f"relative link must stay in the vendor folder '{page.vendor}': {href}")
		if not resolved.is_file():
			self.error(page.path, number, f"link target does not exist: {href}")
			return
		self.check_link_is_base_page(page, number, href, resolved)

	def check_link_is_base_page(self, page, number, href, resolved):
		if resolved.name == "README.md":
			self.error(page.path, number, f"README.md is not a page: {href}")
			return
		match = PAGE_RE.match(resolved.name)
		if match and (match.group("suffix") or match.group("lang")):
			self.error(page.path, number, f"link to the German base page, not to a variant or translation: {href}")

	def check_image_link(self, page, number, href):
		resolved, target = self.resolve(page, href)
		if resolved is None:
			self.error(page.path, number, f"image leaves client_docs/: {href}")
			return
		relative = resolved.relative_to(self.docs).parts
		match = IMAGE_RE.match(resolved.name)
		if len(relative) != 2 or relative[0] != "images" or not match:
			self.error(page.path, number, f"image must be a WebP file in client_docs/images/ with a valid name: {href}")
			return
		if not resolved.is_file() and match.group("name") not in self.shot_names:
			self.error(page.path, number, f"image '{resolved.name}' does not exist and has no entry in shots.yaml")

	def check_shots(self):
		path = self.docs / "shots.yaml"
		if not path.is_file():
			self.error(self.docs, 1, "missing client_docs/shots.yaml (use 'shots: []' when there are no screenshots)")
			return
		data, problem = parse_yaml(path.read_text(encoding="utf-8", errors="replace"))
		if problem:
			self.error(path, problem[0], problem[1])
			return
		if not isinstance(data, dict) or "shots" not in data or not set(data) <= {"shots", "profile"}:
			self.error(path, 1, "shots.yaml must be a map with the key 'shots' and the optional key 'profile'")
			return
		self.report_duplicates(path, data)
		self.check_shot_profile(path, data)
		if not isinstance(data["shots"], list):
			self.error(path, data.key_lines["shots"], "'shots' must be a list")
			return
		for shot in data["shots"]:
			self.check_shot(path, shot, data.key_lines["shots"])
		self.check_shot_names(path, data["shots"])

	def check_shot_profile(self, path, data):
		profile = data.get("profile", "full")
		line = data.key_lines.get("profile", 1)
		if profile not in SHOT_PROFILES:
			self.error(path, line, f"'profile' must be one of {', '.join(SHOT_PROFILES)}")
		elif self.is_docs_app and profile != "base" and data.get("shots"):
			self.error(path, line, "vendor screenshots need 'profile: base', so that no other app shows in them")

	def check_shot_names(self, path, shots):
		seen = {}
		for shot in shots:
			name = shot.get("name") if isinstance(shot, dict) else None
			if not isinstance(name, str):
				continue
			if name in seen:
				self.error(path, shot.key_lines["name"], f"duplicate shot name '{name}' (first at line {seen[name]})")
			seen.setdefault(name, shot.key_lines["name"])
			self.shot_names.add(name)
			if self.is_docs_app:
				self.check_vendor_prefix(path, shot, name)

	def check_vendor_prefix(self, path, shot, name):
		vendors = [folder.name for folder in self.docs.iterdir() if folder.is_dir() and folder.name != "images"]
		if not any(name.startswith(vendor.replace("_", "-") + "-") for vendor in vendors):
			self.error(path, shot.key_lines["name"], f"shot name '{name}' must start with a vendor folder name")

	def check_shot(self, path, shot, fallback_line):
		if not isinstance(shot, dict):
			self.error(path, fallback_line, "each shot must be a map")
			return
		self.report_unknown_keys(path, shot, SHOT_KEYS)
		self.report_duplicates(path, shot)
		self.check_shot_required(path, shot)
		self.check_shot_optional(path, shot)
		for step in shot.get("steps", []) if isinstance(shot.get("steps"), list) else []:
			self.check_step(path, step, shot.key_lines.get("steps", shot.line))
		if "crop" in shot:
			self.check_target(path, shot["crop"], shot.key_lines["crop"], "crop")

	def check_shot_required(self, path, shot):
		for key in ("name", "route"):
			if key not in shot:
				self.error(path, shot.line, f"shot is missing required key '{key}'")
		name = shot.get("name")
		if "name" in shot and not (isinstance(name, str) and SHOT_NAME_RE.match(name)):
			self.error(path, shot.key_lines["name"], f"shot name must match {SHOT_NAME_RE.pattern}")
		route = shot.get("route")
		if "route" in shot and not (isinstance(route, str) and route.startswith("/")):
			self.error(path, shot.key_lines["route"], "'route' must be a string starting with /")

	def check_shot_optional(self, path, shot):
		line = shot.key_lines.get
		if "user" in shot and shot["user"] not in SHOT_USERS:
			self.error(path, line("user"), f"'user' must be one of {', '.join(SHOT_USERS)}")
		for key in ("wait", "css"):
			if key in shot and not is_text(shot[key]):
				self.error(path, line(key), f"'{key}' must be a non-empty string")
		if "settle" in shot and not (is_integer(shot["settle"]) and shot["settle"] >= 0):
			self.error(path, line("settle"), "'settle' must be a non-negative integer (ms)")
		if "steps" in shot and not isinstance(shot["steps"], list):
			self.error(path, line("steps"), "'steps' must be a list")
		if "hideText" in shot:
			self.check_hide_text(path, shot["hideText"], line("hideText"))

	def check_hide_text(self, path, patterns, line):
		if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
			self.error(path, line, "'hideText' must be a list of regular expressions")
			return
		for pattern in patterns:
			try:
				re.compile(pattern)
			except re.error as error:
				self.error(path, line, f"invalid regular expression {pattern!r}: {error}")

	def check_step(self, path, step, line):
		if not isinstance(step, dict):
			self.error(path, line, "each step must be a map with one key")
			return
		line = step.line
		action = [key for key in step if key in STEPS]
		extra = [key for key in step if key not in STEPS and not (key == "value")]
		if len(action) != 1 or extra:
			self.error(path, line, f"a step has exactly one of {', '.join(STEPS)}; got keys {', '.join(map(str, step))}")
			return
		if action[0] == "fill":
			self.check_fill(path, step, line)
		else:
			if "value" in step:
				self.error(path, line, f"step '{action[0]}' takes no 'value'")
			self.check_target(path, step[action[0]], line, action[0])

	def check_fill(self, path, step, line):
		target = step["fill"]
		if not isinstance(target, dict) or "value" not in target:
			self.error(path, line, "step 'fill' needs a target plus 'value'")
			return
		if not isinstance(target["value"], (str, int, float)) or isinstance(target["value"], bool):
			self.error(path, line, "'value' of step 'fill' must be a string or number")
		rest = LDict({key: item for key, item in target.items() if key != "value"})
		rest.line = target.line
		self.check_target(path, rest, line, "fill")

	def check_target(self, path, target, line, owner):
		if is_text(target):
			return
		if not isinstance(target, dict):
			self.error(path, line, f"'{owner}' needs a target: a label string or one of label, field, selector")
			return
		keys = [key for key in target if key in TARGET_KEYS]
		extra = [key for key in target if key not in TARGET_KEYS]
		if len(keys) != 1 or extra or not is_text(target[keys[0]] if keys else None):
			self.error(path, getattr(target, "line", line), f"target of '{owner}' must have exactly one non-empty key of label, field, selector")

	def run_markdownlint(self):
		config = Path(__file__).with_name(".markdownlint.jsonc")
		command = [
			"npx",
			"--yes",
			f"markdownlint-cli2@{self.markdownlint_version}",
			"--config",
			str(config),
			"client_docs/**/*.md",
		]
		try:
			result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
		except FileNotFoundError:
			self.error(self.docs, 1, "markdownlint could not run: npx not found")
			return
		self.report_markdownlint(result)

	def report_markdownlint(self, result):
		pattern = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?::\d+)? (?:error )?(?P<rule>MD\d+\S*) (?P<text>.*)$")
		found = 0
		for line in (result.stdout + "\n" + result.stderr).splitlines():
			match = pattern.match(line.strip())
			if match:
				found += 1
				self.error(match.group("file"), int(match.group("line")), f"markdownlint {match.group('rule')} {match.group('text')}")
		if result.returncode not in (0, 1) or (result.returncode == 1 and not found):
			tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
			self.error(self.docs, 1, "markdownlint failed to run: " + " | ".join(tail))

	def check_covers(self):
		if not (self.base and self.head):
			return
		if self.is_docs_app:
			return
		if "no-docs" in self.labels:
			return
		git = GitDiff(self.root, self.base, self.head)
		changes = Changes(git)
		changed_docs = {path for path in changes.paths if path.startswith("client_docs/")}
		for group, members in self.cover_groups().items():
			self.check_group_covers(group, members, changes, changed_docs)

	def cover_groups(self):
		groups = {}
		for page in self.pages:
			if page.base:
				groups.setdefault(page.group, []).append(page)
		return groups

	def check_group_covers(self, group, members, changes, changed_docs):
		prefix = "client_docs/" + (group[0] + "/" if group[0] != "." else "")
		touched = any(self.in_group(path, prefix, group[1]) for path in changed_docs)
		if touched:
			return
		for page in members:
			if page.lang or not isinstance(page.data, dict):
				continue
			for entry in page.data.get("covers") or []:
				hit = changes.match(entry) if isinstance(entry, str) else None
				if hit:
					self.error(page.path, page.data.key_lines.get("covers", 2), f"covered item '{entry}' changed in {hit}, but no file of this page changed; update the page or add the label 'no-docs'")


	def in_group(self, path, prefix, stem):
		if not path.startswith(prefix) or "/" in path[len(prefix):]:
			return False
		match = PAGE_RE.match(path[len(prefix):])
		return bool(match) and match.group("base") == stem


class GitDiff:
	def __init__(self, root, base, head):
		self.root = root
		self.head = head
		self.base = self.git("merge-base", base, head).strip() or base

	def git(self, *args):
		result = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True)
		return result.stdout if result.returncode == 0 else ""

	def changed(self):
		output = self.git("diff", "--name-status", "--no-renames", "-z", self.base, self.head)
		tokens = [token for token in output.split("\0") if token]
		return [(tokens[index], tokens[index + 1]) for index in range(0, len(tokens) - 1, 2)]

	def show(self, rev, path):
		result = subprocess.run(["git", "-C", str(self.root), "show", f"{rev}:{path}"], capture_output=True, text=True)
		return result.stdout if result.returncode == 0 else None


class Changes:
	def __init__(self, git):
		self.git = git
		self.status = dict((path, status) for status, path in git.changed())
		self.pairs = {}
		self.paths = [path for path in self.status if self.is_real_change(path)]

	def pair(self, path):
		if path not in self.pairs:
			self.pairs[path] = (self.load(self.git.base, path), self.load(self.git.head, path))
		return self.pairs[path]

	def load(self, rev, path):
		text = self.git.show(rev, path)
		if text is None:
			return None
		try:
			return strip_modified(json.loads(text))
		except ValueError:
			return text

	def is_real_change(self, path):
		if not path.endswith(".json"):
			return True
		before, after = self.pair(path)
		return before is None or after is None or before != after

	def match(self, entry):
		kind, _, value = entry.partition(":")
		matcher = getattr(self, f"match_{kind}", None)
		return matcher(value) if matcher else None

	def first(self, pattern):
		regex = re.compile(pattern) if isinstance(pattern, str) else pattern
		return next((path for path in self.paths if regex.fullmatch(path)), None)

	def match_doctype(self, value):
		name = scrub(value)
		for path in self.paths:
			if re.fullmatch(rf"(?:.*/)?doctype/{re.escape(name)}/{re.escape(name)}\.json", path) and self.fields_changed(path):
				return path
			if re.fullmatch(r"(?:.*/)?fixtures/custom_field\.json", path) and self.entries_changed(path, "dt", value):
				return path
			if re.fullmatch(r"(?:.*/)?fixtures/property_setter\.json", path) and self.entries_changed(path, "doc_type", value):
				return path
		return None

	def fields_changed(self, path):
		before, after = self.pair(path)
		if not isinstance(before, dict) or not isinstance(after, dict):
			return True
		return any(before.get(key) != after.get(key) for key in ("fields", "field_order"))

	def entries_changed(self, path, key, value):
		before, after = self.pair(path)
		return self.entries(before, key, value) != self.entries(after, key, value)

	def entries(self, data, key, value):
		if not isinstance(data, list):
			return []
		return sorted(json.dumps(item, sort_keys=True) for item in data if isinstance(item, dict) and item.get(key) == value)

	def match_report(self, value):
		return self.first(rf"(?:.*/)?report/{re.escape(scrub(value))}/.+")

	def match_page(self, value):
		return self.first(rf"(?:.*/)?page/{re.escape(scrub(value))}/.+")

	def match_print_format(self, value):
		hit = self.first(rf"(?:.*/)?print_format/{re.escape(scrub(value))}/.+")
		return hit or self.fixture_entry("print_format.json", value)

	def match_workspace(self, value):
		name = re.escape(scrub(value))
		hit = self.first(rf"(?:.*/)?workspace/{name}/{name}\.json")
		return hit or self.fixture_entry("workspace.json", value)

	def fixture_entry(self, filename, value):
		for path in self.paths:
			if re.fullmatch(rf"(?:.*/)?fixtures/{re.escape(filename)}", path) and self.entries_changed(path, "name", value):
				return path
		return None

	def match_www(self, value):
		return self.first(rf"[^/]+/www/{re.escape(value)}(?:\.[^/]*|/.*)")

	def match_js(self, value):
		return value if value in self.paths else None

	def match_file(self, value):
		return self.first(glob_to_regex(value))


def format_finding(file, line, message, annotations):
	if not annotations:
		return f"{file}:{line}: {message}" if line else f"{file}: {message}"
	escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
	location = f"file={file}" + (f",line={line}" if line else "")
	return f"::error {location}::{escaped}"


def parse_labels(raw):
	if not raw:
		return []
	try:
		labels = json.loads(raw)
	except ValueError:
		return [label.strip() for label in raw.split(",") if label.strip()]
	return [label for label in labels if isinstance(label, str)] if isinstance(labels, list) else []


def build_parser():
	parser = argparse.ArgumentParser(description="Check customer docs in client_docs/ against CONVENTIONS.md")
	parser.add_argument("--root", default=".", help="repo root that holds client_docs/")
	parser.add_argument("--app", help="app name of the repo (default: detected)")
	parser.add_argument("--base", help="PR base commit for the covers check")
	parser.add_argument("--head", help="PR head commit for the covers check")
	parser.add_argument("--labels", default=os.environ.get("PR_LABELS", ""), help="PR labels as JSON list")
	parser.add_argument("--skip-markdownlint", action="store_true")
	parser.add_argument("--markdownlint-version", default=MARKDOWNLINT_CLI2_VERSION)
	parser.add_argument("--format", choices=("auto", "github", "plain"), default="auto")
	return parser


def main(argv=None):
	options = build_parser().parse_args(argv)
	checker = Checker(
		options.root,
		app=options.app,
		base=options.base,
		head=options.head,
		labels=parse_labels(options.labels),
		markdownlint=not options.skip_markdownlint,
		markdownlint_version=options.markdownlint_version,
	)
	findings = checker.run()
	annotations = options.format == "github" or (options.format == "auto" and os.environ.get("GITHUB_ACTIONS") == "true")
	for file, line, message in findings.items:
		print(format_finding(file, line, message, annotations))
	if not checker.visible_files():
		print("No customer docs in client_docs/: nothing to check.")
	elif len(findings):
		print(f"{len(findings)} problem(s) found.", file=sys.stderr)
	else:
		print("Customer docs checks passed.")
	return 1 if len(findings) else 0


if __name__ == "__main__":
	sys.exit(main())

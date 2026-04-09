"""
C Unit Test Runner — Ceedling + Unity + CMock
Copies ALL project headers into the build so transitive includes always resolve.
Requirements: Python 3.8+, Ruby, gem install ceedling, gcc in PATH
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os, re, json, subprocess, threading, shutil
from pathlib import Path

# ─── Theme ────────────────────────────────────────────────────────────────────
BG      = "#1e1e2e"; BG2 = "#2a2a3e"; BG3 = "#313145"
ACCENT  = "#7c6af7"; ACCENT2 = "#a78bfa"
GREEN   = "#4ade80"; RED = "#f87171"; YELLOW = "#facc15"
TEXT    = "#e2e8f0"; TEXT2 = "#94a3b8"; BORDER = "#3f3f5c"
MONO    = ("Consolas", 11); SANS = ("Segoe UI", 10)
SANS_B  = ("Segoe UI", 10, "bold"); SANS_LG = ("Segoe UI", 13, "bold")

SKIP_DIRS = {"_ctest_build", "build", "_build", ".git", "vendor", "test", "tests"}

# ─── C parser ─────────────────────────────────────────────────────────────────
FUNC_RE = re.compile(
    r'^\s*(?:(?:static|inline|extern|const)\s+)*'
    r'(?P<ret>(?:unsigned\s+)?(?:int|long|short|char|float|double|void|bool|'
    r'uint8_t|uint16_t|uint32_t|int8_t|int16_t|int32_t|size_t|[A-Z_][A-Z0-9_]*)\s*\*?)\s+'
    r'(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s*\((?P<params>[^)]*)\)\s*\{',
    re.MULTILINE)
KEYWORDS = {"if","for","while","switch","return","else","do","case","default",
            "break","continue","goto","typedef","struct","enum","union"}

def parse_functions(path):
    try:
        src = Path(path).read_text(encoding="utf-8", errors="ignore")
        src = re.sub(r'/\*.*?\*/', '', src, flags=re.DOTALL)
        src = re.sub(r'//[^\n]*', '', src)
        fns = []
        for m in FUNC_RE.finditer(src):
            name = m.group("name")
            if name in KEYWORDS:
                continue
            fns.append({
                "name":   name,
                "ret":    m.group("ret").strip(),
                "params": [p.strip() for p in m.group("params").split(",")
                           if p.strip() and p.strip() != "void"],
                "file":   str(path),
            })
        return fns
    except Exception:
        return []

def scan_project(folder):
    fns = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".c") or f.endswith(".h"):
                fns.extend(parse_functions(os.path.join(root, f)))
    seen, unique = set(), []
    for fn in fns:
        if fn["name"] not in seen:
            seen.add(fn["name"]); unique.append(fn)
    return unique

def collect_all_headers(folder):
    """Return list of all .h Paths in the project (skipping build dirs)."""
    result = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".h"):
                result.append(Path(root) / f)
    return result

def collect_all_header_dirs(folder):
    """Return every directory that contains at least one .h file."""
    dirs_with_h = set()
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if any(f.endswith(".h") for f in files):
            dirs_with_h.add(root)
    return dirs_with_h

# ─── Test store ───────────────────────────────────────────────────────────────
class TestStore:
    def __init__(self):
        self.project_folder = ""
        self.functions = []
        self.tests = {}
        self._save_path = ""

    def set_project(self, folder):
        self.project_folder = folder
        self.functions = scan_project(folder)
        self._save_path = os.path.join(folder, ".ctest_runner.json")
        self._load()

    def _load(self):
        if os.path.exists(self._save_path):
            try:
                self.tests = json.loads(
                    Path(self._save_path).read_text()).get("tests", {})
            except Exception:
                pass

    def save(self):
        if self._save_path:
            Path(self._save_path).write_text(
                json.dumps({"tests": self.tests}, indent=2))

    def add_test(self, fn_name, inputs, expected, label="", mocks=None):
        if fn_name not in self.tests:
            self.tests[fn_name] = []
        self.tests[fn_name].append({
            "inputs":   inputs,
            "expected": expected,
            "label":    label or f"test_{len(self.tests[fn_name])+1}",
            "mocks":    mocks or [],
        })
        self.save()

    def remove_test(self, fn_name, idx):
        if fn_name in self.tests and 0 <= idx < len(self.tests[fn_name]):
            self.tests[fn_name].pop(idx)
            self.save()

    def get_tests(self, fn_name):
        return self.tests.get(fn_name, [])

# ─── Code generation ──────────────────────────────────────────────────────────
def sanitize_label(s):
    return re.sub(r'[^a-zA-Z0-9_]', '_', s)

def mock_expect_lines(mock):
    """Turn one mock dict into CMock expectation call(s)."""
    fn    = mock.get("fn", "")
    args  = mock.get("args", "").strip()
    ret   = mock.get("returns", "").strip()
    times = max(1, int(mock.get("times", 1)))
    ignore = mock.get("ignore_args", False)
    lines = []
    for _ in range(times):
        if ignore:
            lines.append(f"{fn}_IgnoreAndReturn({ret});" if ret else f"{fn}_Ignore();")
        elif ret:
            arg_part = f"{args}, " if args else ""
            lines.append(f"{fn}_ExpectAndReturn({arg_part}{ret});")
        elif args:
            lines.append(f"{fn}_Expect({args});")
        else:
            lines.append(f"{fn}_Expect();")
    return lines

def generate_test_c(fn_info, test_cases, mock_header_stems, all_project_headers=None):
    """
    all_project_headers: list of header filenames (just the .h names, no path)
    that were copied into _ctest_build/src/.  Every one gets #included so the
    test file always has all type definitions and declarations it needs.
    """
    fn  = fn_info["name"]
    ret = fn_info["ret"]

    lines = ["/* Auto-generated by C Test Runner */", '#include "unity.h"']

    # CMock stubs first
    for stem in mock_header_stems:
        lines.append(f'#include "Mock{stem}.h"')

    # Include ALL project headers so every typedef/declaration is visible.
    # If we have the full list use it; otherwise fall back to the sibling header.
    if all_project_headers:
        mock_generated = {f"Mock{s}.h" for s in mock_header_stems}
        for hname in sorted(all_project_headers):
            if hname not in mock_generated:
                lines.append(f'#include "{hname}"')
    else:
        source_header = Path(fn_info["file"]).with_suffix(".h")
        if source_header.exists():
            lines.append(f'#include "{source_header.name}"')
        else:
            params = ", ".join(fn_info["params"]) if fn_info["params"] else "void"
            lines.append(f"/* no header found — forward declaration */")
            lines.append(f"{ret} {fn}({params});")

    lines += ["", "void setUp(void) {}", "void tearDown(void) {}", ""]

    for i, tc in enumerate(test_cases):
        label    = sanitize_label(tc.get("label", f"test_{i+1}"))
        inputs   = tc.get("inputs", [])
        expected = tc.get("expected", "0")
        mocks    = tc.get("mocks", [])
        args     = ", ".join(str(x) for x in inputs)
        lines.append(f"void test_{fn}_{label}(void) {{")
        for mock in mocks:
            for exp in mock_expect_lines(mock):
                lines.append(f"    {exp}")
        if ret.strip() == "void":
            lines += [f"    {fn}({args});", "    TEST_PASS();"]
        elif ret.strip() in ("float", "double"):
            lines.append(f"    TEST_ASSERT_EQUAL_FLOAT({expected}, {fn}({args}));")
        elif "char*" in ret or "char *" in ret:
            lines.append(f'    TEST_ASSERT_EQUAL_STRING("{expected}", {fn}({args}));')
        else:
            lines.append(f"    TEST_ASSERT_EQUAL({expected}, {fn}({args}));")
        lines += ["}", ""]
    return "\n".join(lines)

# ─── Ceedling helpers ─────────────────────────────────────────────────────────
def write_project_yml(build_dir, mock_headers):
    """
    Write a project.yml that enables CMock and lists which headers to mock.
    """
    mock_paths_yml = ""
    if mock_headers:
        # Note: Added correct indentation for YAML lists
        mock_paths_yml = "\n  :mock_path:\n    - src/**"

    mocks_block = ""
    if mock_headers:
        # Fixed boolean case (TRUE -> true) and symbols
        mocks_block = """
:cmock:
  :mock_prefix: Mock
  :when_no_prototypes: :warn
  :enforce_strict_ordering: true
  :plugins:
    - :ignore
    - :ignore_arg
    - :expect_any_args
    - :return_thru_ptr
    - :array
    - :callback"""

    # --- THE CRITICAL CHANGE ---
    # We remove the hardcoded load_paths. If Ceedling is installed correctly, 
    # it knows where its own plugins are. If you leave it empty, it uses defaults.
    yml = f""":project:
  :use_exceptions: false
  :use_test_preprocessor: :all
  :build_root: build
  :release_build: false
  :test_file_prefix: test_

:paths:
  :test:
    - test/**
  :source:
    - src/**
  :include:
    - src/**{mock_paths_yml}

:defines:
  :test: []
{mocks_block}

:plugins:
  :load_paths:
    - 'C:/Users/HP/.local/share/gem/ruby/4.0.0/gems/ceedling-1.0.1/plugins'
  :enabled:
    - report_tests_pretty_stdout
    - module_generator
"""
    (build_dir / "project.yml").write_text(yml)


def collect_all_sources(folder):
    """Return all .c Paths in the project (skipping build dirs)."""
    result = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".c"):
                result.append(Path(root) / f)
    return result


def strip_main_from_text(src):
    """Remove main() body so it does not clash with Unity runner."""
    m = re.compile(r'\bint\s+main\s*\([^)]*\)\s*\{', re.MULTILINE).search(src)
    if not m:
        return src, False
    i = src.index('{', m.start())
    depth = 0
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        i += 1
    else:
        end = len(src)
    placeholder = "/* main() removed */\n" * max(1, src[m.start():end].count('\n'))
    return src[:m.start()] + placeholder + src[end:], True


def setup_ceedling_project(project_folder, fn_info, test_cases):
    """
    Prepare _ctest_build/ for Ceedling.

    src/  -- ALL .c files from the project (main() stripped) + ALL .h files (flat)
    test/ -- generated test file
    project.yml

    Copying everything flat ensures:
      - The function under test is found by the linker even when .c and .h
        are in different subdirectories.
      - common_include.h and all transitive headers are resolved by both
        gcc and CMock's Ruby parser.
    """
    build_dir = Path(project_folder) / "_ctest_build"
    (build_dir / "src").mkdir(parents=True, exist_ok=True)
    (build_dir / "test").mkdir(parents=True, exist_ok=True)

    fn_name = fn_info["name"]

    # Copy ALL .c files, stripping main() from any that have it
    all_sources = collect_all_sources(project_folder)
    copied_c, stripped_mains = [], []
    for src_path in all_sources:
        text = src_path.read_text(encoding="utf-8", errors="ignore")
        clean, had_main = strip_main_from_text(text)
        dest = build_dir / "src" / src_path.name
        dest.write_text(clean, encoding="utf-8")
        copied_c.append(src_path.name)
        if had_main:
            stripped_mains.append(src_path.name)

    # Copy ALL .h files flat into src/
    all_headers = collect_all_headers(project_folder)
    for h in all_headers:
        shutil.copy2(h, build_dir / "src" / h.name)

    # Collect mock stems from test cases
    mock_header_stems = sorted({
        m["header"].strip()
        for tc in test_cases
        for m in tc.get("mocks", [])
        if m.get("header", "").strip()
    })

    # Build flat list of all header filenames now sitting in src/
    all_header_names = [h.name for h in all_headers]

    # Write test file — pass all header names so every .h gets #included
    test_c    = generate_test_c(fn_info, test_cases, mock_header_stems, all_header_names)
    test_file = build_dir / "test" / f"test_{fn_name}.c"
    test_file.write_text(test_c, encoding="utf-8")

    # Write project.yml
    write_project_yml(build_dir, mock_header_stems)

    return build_dir, test_file, mock_header_stems, copied_c, stripped_mains


def run_with_ceedling(project_folder, fn_info, test_cases):
    build_dir, test_file, mock_headers, copied_c, stripped = setup_ceedling_project(
        project_folder, fn_info, test_cases)
    fn_name = fn_info["name"]

    yield "INFO", f"Build dir : {build_dir}\n"
    yield "INFO", f"Source files copied into src/: {len(copied_c)}\n"
    if stripped:
        yield "INFO", f"main() stripped from: {', '.join(stripped)}\n"
    if mock_headers:
        yield "INFO", f"CMock stubs for: {', '.join(mock_headers)}\n"
    yield "INFO", f"Running: ceedling test:test_{fn_name}\n\n"

    ceedling_cmd = shutil.which("ceedling") or "ceedling"
    try:
        proc = subprocess.Popen(
            [ceedling_cmd, f"test:test_{fn_name}"],
            cwd=str(build_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        for line in proc.stdout:
            yield "RESULT", line
        proc.wait()
        if proc.returncode not in (0, 1):
            yield "ERROR", f"\nCeedling exited with code {proc.returncode}\n"
    except FileNotFoundError:
        yield "ERROR", (
            "ceedling not found in PATH.\n"
            "Run:  gem install ceedling\n"
            "Then restart this tool.\n")

# ─── Unity-only fallback ──────────────────────────────────────────────────────
def strip_main(src):
    m = re.compile(r'\bint\s+main\s*\([^)]*\)\s*\{', re.MULTILINE).search(src)
    if not m:
        return src
    i = src.index('{', m.start())
    depth = 0
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        i += 1
    else:
        end = len(src)
    placeholder = "/* main() removed */\n" * max(1, src[m.start():end].count('\n'))
    return src[:m.start()] + placeholder + src[end:]


def run_with_unity_directly(project_folder, fn_info, test_cases):
    """gcc + Unity only — no CMock, but all header dirs are passed to -I."""
    build_dir   = Path(project_folder) / "_ctest_build"
    build_dir.mkdir(exist_ok=True)
    source_file = Path(fn_info["file"])
    fn_name     = fn_info["name"]

    # Download Unity if missing
    unity_dir = build_dir / "unity"
    unity_c   = unity_dir / "unity.c"
    if not unity_c.exists():
        yield "INFO", "Downloading Unity...\n"
        unity_dir.mkdir(exist_ok=True)
        try:
            import urllib.request
            base = "https://raw.githubusercontent.com/ThrowTheSwitch/Unity/master/src/"
            for fname in ("unity.c", "unity.h", "unity_internals.h"):
                urllib.request.urlretrieve(base + fname, unity_dir / fname)
            yield "INFO", "Unity downloaded.\n"
        except Exception as e:
            yield "ERROR", f"Could not download Unity: {e}\n"
            return

    if any(tc.get("mocks") for tc in test_cases):
        yield "INFO", (
            "WARNING: mocks defined but running in Unity-only mode.\n"
            "Enable 'Use Ceedling+CMock' and install Ceedling for real mock support.\n\n")

    # Strip main
    yield "INFO", f"Preprocessing {source_file.name}...\n"
    stripped = strip_main(source_file.read_text(encoding="utf-8", errors="ignore"))
    testable = build_dir / (source_file.stem + "_testable.c")
    testable.write_text(stripped, encoding="utf-8")

    # Generate test file (no Mock includes in Unity-only mode)
    header = source_file.with_suffix(".h")
    if header.exists():
        inc = f'#include "{header.name}"'
    else:
        params = ", ".join(fn_info["params"]) if fn_info["params"] else "void"
        inc = f"{fn_info['ret']} {fn_name}({params});"

    lines = ["/* Auto-generated — Unity only */", '#include "unity.h"', inc, "",
             "void setUp(void) {}", "void tearDown(void) {}", ""]
    for i, tc in enumerate(test_cases):
        label = sanitize_label(tc.get("label", f"test_{i+1}"))
        args  = ", ".join(str(x) for x in tc.get("inputs", []))
        exp   = tc.get("expected", "0")
        ret   = fn_info["ret"]
        lines.append(f"void test_{fn_name}_{label}(void) {{")
        if ret.strip() == "void":
            lines += [f"    {fn_name}({args});", "    TEST_PASS();"]
        elif ret.strip() in ("float", "double"):
            lines.append(f"    TEST_ASSERT_EQUAL_FLOAT({exp}, {fn_name}({args}));")
        elif "char*" in ret or "char *" in ret:
            lines.append(f'    TEST_ASSERT_EQUAL_STRING("{exp}", {fn_name}({args}));')
        else:
            lines.append(f"    TEST_ASSERT_EQUAL({exp}, {fn_name}({args}));")
        lines += ["}", ""]
    test_file = build_dir / f"test_{fn_name}.c"
    test_file.write_text("\n".join(lines), encoding="utf-8")

    # Runner
    test_fns = [f"test_{fn_name}_{sanitize_label(tc.get('label', f'test_{i+1}'))}"
                for i, tc in enumerate(test_cases)]
    runner = build_dir / f"runner_{fn_name}.c"
    rb  = '#include "unity.h"\n'
    rb += "".join(f"extern void {f}(void);\n" for f in test_fns)
    rb += "\nint main(void) {\n    UNITY_BEGIN();\n"
    rb += "".join(f"    RUN_TEST({f});\n" for f in test_fns)
    rb += "    return UNITY_END();\n}\n"
    runner.write_text(rb, encoding="utf-8")

    # -I: unity dir + every directory in the project that has a .h file
    header_dirs = {str(unity_dir)}
    header_dirs.update(collect_all_header_dirs(project_folder))
    includes = [f"-I{d}" for d in sorted(header_dirs)]

    exe = build_dir / f"test_{fn_name}.exe"
    cmd = ["gcc", "-std=c99", "-Wall",
           str(test_file), str(runner), str(unity_c), str(testable),
           *includes, "-o", str(exe)]

    yield "INFO", (f"Include dirs ({len(includes)}):\n" +
                   "".join(f"  {p}\n" for p in includes) + "\n")
    yield "CMD", "Compiling:\n  " + "\n  ".join(cmd) + "\n\n"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(build_dir))
        if r.stdout: yield "OUT", r.stdout
        if r.stderr: yield "ERR", r.stderr + "\n"
        if r.returncode != 0:
            yield "ERROR", f"Compilation failed (exit {r.returncode})\n"
            return
        yield "INFO", "Compilation OK. Running tests...\n\n"
        r2 = subprocess.run([str(exe)], capture_output=True, text=True,
                            cwd=str(build_dir))
        if r2.stdout: yield "RESULT", r2.stdout
        if r2.stderr: yield "ERR", r2.stderr
    except FileNotFoundError:
        yield "ERROR", ("gcc not found in PATH.\n"
                        "Install MinGW-w64 from https://winlibs.com\n")

# ─── CMock Expectations dialog ────────────────────────────────────────────────
class MockEditorDialog(tk.Toplevel):
    def __init__(self, parent, existing_mocks=None):
        super().__init__(parent)
        self.title("CMock Expectations Editor")
        self.geometry("900x460")
        self.configure(bg=BG)
        self.result = None
        self._rows  = []
        self._build_ui(existing_mocks or [])
        self.transient(parent)
        self.grab_set()

    def _build_ui(self, existing):
        hdr = tk.Frame(self, bg=BG2, pady=8, padx=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="CMock Expectations", font=SANS_LG,
                 bg=BG2, fg=ACCENT2).pack(side="left")
        tk.Label(hdr, text="  One row = one mock call.  CMock verifies order + args.",
                 font=("Segoe UI", 9), bg=BG2, fg=TEXT2).pack(side="left")

        col_hdr = tk.Frame(self, bg=BG3, pady=4)
        col_hdr.pack(fill="x", padx=8)
        for txt, w in [("Header stem", 13), ("Function", 15), ("Args", 17),
                       ("Returns", 9), ("Times", 5), ("Ignore args", 10)]:
            tk.Label(col_hdr, text=txt, font=("Segoe UI", 9, "bold"),
                     bg=BG3, fg=TEXT2, width=w, anchor="w").pack(side="left", padx=4)

        self._canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True, padx=8)
        self._rows_frame = tk.Frame(self._canvas, bg=BG)
        self._canvas.create_window((0, 0), window=self._rows_frame, anchor="nw")
        self._rows_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        for m in existing:
            self._add_row(m)

        btn_row = tk.Frame(self, bg=BG2, pady=8)
        btn_row.pack(fill="x", padx=8)
        tk.Button(btn_row, text="+ Add", font=SANS_B, bg=ACCENT, fg="white",
                  relief="flat", padx=12, pady=4, cursor="hand2",
                  command=self._add_row).pack(side="left")
        tk.Button(btn_row, text="Save", font=SANS_B, bg=GREEN, fg="#0a0a0a",
                  relief="flat", padx=16, pady=4, cursor="hand2",
                  command=self._save).pack(side="right", padx=4)
        tk.Button(btn_row, text="Cancel", font=SANS, bg=BG3, fg=TEXT2,
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  command=self.destroy).pack(side="right", padx=4)

    def _add_row(self, data=None):
        data = data or {}
        row = tk.Frame(self._rows_frame, bg=BG2, pady=3)
        row.pack(fill="x", pady=2)

        def ent(w, val=""):
            e = tk.Entry(row, font=MONO, bg=BG3, fg=TEXT,
                         insertbackground=TEXT, relief="flat",
                         highlightthickness=1, highlightcolor=ACCENT, width=w)
            e.insert(0, val)
            e.pack(side="left", padx=3)
            return e

        e_h  = ent(13, data.get("header", ""))
        e_fn = ent(15, data.get("fn", ""))
        e_a  = ent(17, data.get("args", ""))
        e_r  = ent(9,  data.get("returns", ""))
        e_t  = ent(5,  str(data.get("times", 1)))
        ig   = tk.BooleanVar(value=data.get("ignore_args", False))
        tk.Checkbutton(row, variable=ig, bg=BG2, activebackground=BG2,
                       fg=TEXT2, selectcolor=BG3).pack(side="left", padx=8)

        def rm():
            row.destroy()
            self._rows[:] = [r for r in self._rows if r[0].winfo_exists()]
        tk.Button(row, text="✕", font=("Segoe UI", 9), bg=BG3, fg=RED,
                  relief="flat", cursor="hand2", command=rm).pack(side="left", padx=4)
        self._rows.append((e_h, e_fn, e_a, e_r, e_t, ig))

    def _save(self):
        mocks = []
        for e_h, e_fn, e_a, e_r, e_t, ig in self._rows:
            if not e_h.winfo_exists():
                continue
            fn = e_fn.get().strip()
            if not fn:
                continue
            try:
                times = int(e_t.get().strip())
            except ValueError:
                times = 1
            mocks.append({
                "header":      e_h.get().strip(),
                "fn":          fn,
                "args":        e_a.get().strip(),
                "returns":     e_r.get().strip(),
                "times":       times,
                "ignore_args": ig.get(),
            })
        self.result = mocks
        self.destroy()

# ─── Main App ─────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("C Unit Test Runner — Ceedling + Unity + CMock")
        self.geometry("1280x840")
        self.minsize(900, 640)
        self.configure(bg=BG)
        self.store          = TestStore()
        self.selected_fn    = None
        self._pending_mocks = []
        self._use_ceedling  = tk.BooleanVar(value=True)
        self._build_ui()

    def _build_ui(self):
        # Top bar
        top = tk.Frame(self, bg=BG2, pady=8, padx=14)
        top.pack(fill="x")
        tk.Label(top, text="⬡ C Test Runner", font=("Segoe UI", 14, "bold"),
                 bg=BG2, fg=ACCENT2).pack(side="left")
        tk.Checkbutton(top, text="Use Ceedling+CMock (recommended)",
                       variable=self._use_ceedling, font=SANS, bg=BG2, fg=TEXT2,
                       activebackground=BG2, selectcolor=BG3,
                       activeforeground=TEXT).pack(side="right", padx=12)
        tk.Button(top, text="📂  Open Project Folder", font=SANS_B, bg=ACCENT,
                  fg="white", relief="flat", padx=14, pady=5, cursor="hand2",
                  command=self.open_folder).pack(side="right", padx=4)
        self.folder_lbl = tk.Label(top, text="No project loaded", font=SANS,
                                   bg=BG2, fg=TEXT2)
        self.folder_lbl.pack(side="right", padx=12)

        pane = tk.PanedWindow(self, orient="horizontal", bg=BG,
                              sashwidth=4, sashrelief="flat")
        pane.pack(fill="both", expand=True, padx=10, pady=(6, 10))

        # Left — function list
        left = tk.Frame(pane, bg=BG2)
        pane.add(left, minsize=220, width=260)
        tk.Label(left, text="Functions", font=SANS_B, bg=BG2, fg=TEXT2,
                 pady=8).pack(fill="x", padx=10)
        self.fn_search = tk.Entry(left, font=SANS, bg=BG3, fg=TEXT,
                                  insertbackground=TEXT, relief="flat",
                                  highlightthickness=1, highlightcolor=ACCENT)
        self.fn_search.pack(fill="x", padx=10, pady=(0, 6))
        self.fn_search.insert(0, "Search...")
        self.fn_search.config(fg=TEXT2)
        self.fn_search.bind("<KeyRelease>", lambda e: self._filter_fns())
        self.fn_search.bind("<FocusIn>",  lambda e: self._clear_search())
        self.fn_search.bind("<FocusOut>", lambda e: self._restore_search())
        fl = tk.Frame(left, bg=BG2)
        fl.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        sb = tk.Scrollbar(fl)
        sb.pack(side="right", fill="y")
        self.fn_listbox = tk.Listbox(
            fl, yscrollcommand=sb.set, font=MONO, bg=BG3, fg=TEXT,
            selectbackground=ACCENT, selectforeground="white", relief="flat",
            bd=0, activestyle="none", highlightthickness=0)
        self.fn_listbox.pack(fill="both", expand=True)
        sb.config(command=self.fn_listbox.yview)
        self.fn_listbox.bind("<<ListboxSelect>>", self.on_fn_select)

        # Right
        right = tk.Frame(pane, bg=BG)
        pane.add(right, minsize=500)

        info_f = tk.Frame(right, bg=BG2, pady=6, padx=12)
        info_f.pack(fill="x", pady=(0, 6))
        self.fn_sig_lbl = tk.Label(info_f, text="← Select a function",
                                   font=MONO, bg=BG2, fg=ACCENT2, anchor="w")
        self.fn_sig_lbl.pack(side="left")
        self.fn_file_lbl = tk.Label(info_f, text="", font=("Segoe UI", 9),
                                    bg=BG2, fg=TEXT2)
        self.fn_file_lbl.pack(side="right")

        # Form
        form = tk.LabelFrame(right, text=" Add Test Case ", font=SANS_B,
                             bg=BG2, fg=TEXT2, bd=1, relief="flat",
                             highlightbackground=BORDER, highlightthickness=1)
        form.pack(fill="x", pady=(0, 6))

        def frow(lbl):
            r = tk.Frame(form, bg=BG2)
            r.pack(fill="x", padx=10, pady=3)
            tk.Label(r, text=lbl, font=SANS, bg=BG2, fg=TEXT2,
                     width=10, anchor="w").pack(side="left")
            return r

        def ent(parent):
            e = tk.Entry(parent, font=MONO, bg=BG3, fg=TEXT,
                         insertbackground=TEXT, relief="flat",
                         highlightthickness=1, highlightcolor=ACCENT)
            e.pack(side="left", fill="x", expand=True)
            return e

        r1 = frow("Label:");    self.e_label    = ent(r1)
        r2 = frow("Inputs:");   self.e_inputs   = ent(r2)
        tk.Label(r2, text="comma-separated", font=("Segoe UI", 9),
                 bg=BG2, fg=TEXT2).pack(side="left", padx=8)
        r3 = frow("Expected:"); self.e_expected = ent(r3)
        tk.Label(r3, text="return value", font=("Segoe UI", 9),
                 bg=BG2, fg=TEXT2).pack(side="left", padx=8)

        r4 = tk.Frame(form, bg=BG2)
        r4.pack(fill="x", padx=10, pady=3)
        self.mock_lbl = tk.Label(r4, text="No mock expectations",
                                 font=("Segoe UI", 9), bg=BG2, fg=TEXT2)
        self.mock_lbl.pack(side="left")
        tk.Button(r4, text="🔧 Edit CMock Expectations", font=SANS,
                  bg=BG3, fg=ACCENT2, relief="flat", padx=10, pady=3,
                  cursor="hand2", command=self._open_mock_editor).pack(side="right")

        br = tk.Frame(form, bg=BG2)
        br.pack(fill="x", padx=10, pady=(4, 10))
        tk.Button(br, text="＋ Add Test Case", font=SANS_B, bg=ACCENT, fg="white",
                  relief="flat", padx=16, pady=5, cursor="hand2",
                  command=self.add_test_case).pack(side="left")
        tk.Button(br, text="Clear", font=SANS, bg=BG3, fg=TEXT2,
                  relief="flat", padx=10, pady=5, cursor="hand2",
                  command=self.clear_form).pack(side="left", padx=8)

        # Table
        tbl = tk.LabelFrame(right, text=" Test Cases ", font=SANS_B, bg=BG2,
                            fg=TEXT2, bd=1, relief="flat",
                            highlightbackground=BORDER, highlightthickness=1)
        tbl.pack(fill="both", expand=True, pady=(0, 6))
        cols = ("label", "inputs", "expected", "mocks")
        self.tree = ttk.Treeview(tbl, columns=cols, show="headings",
                                 selectmode="browse", height=6)
        sty = ttk.Style()
        sty.theme_use("clam")
        sty.configure("Treeview", background=BG3, foreground=TEXT,
                      fieldbackground=BG3, rowheight=26, font=MONO)
        sty.configure("Treeview.Heading", background=BG2, foreground=TEXT2,
                      font=SANS_B, relief="flat")
        sty.map("Treeview", background=[("selected", ACCENT)],
                foreground=[("selected", "white")])
        for c, w in [("label", 140), ("inputs", 200), ("expected", 100), ("mocks", 220)]:
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(tbl, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=4, pady=4)
        tk.Button(tbl, text="🗑 Delete Selected", font=SANS, bg=BG3, fg=RED,
                  relief="flat", padx=10, pady=3, cursor="hand2",
                  command=self.delete_selected_test).pack(
                      anchor="e", padx=8, pady=(0, 6))

        # Run bar
        run_bar = tk.Frame(right, bg=BG)
        run_bar.pack(fill="x", pady=(0, 4))
        self.run_btn = tk.Button(run_bar, text="▶  Run Tests", font=SANS_B,
                                 bg=GREEN, fg="#0a0a0a", relief="flat",
                                 padx=20, pady=7, cursor="hand2",
                                 command=self.run_tests)
        self.run_btn.pack(side="left")
        tk.Button(run_bar, text="📄  View .c", font=SANS, bg=BG3, fg=TEXT2,
                  relief="flat", padx=10, pady=7, cursor="hand2",
                  command=self.view_generated).pack(side="left", padx=8)
        tk.Button(run_bar, text="📋  project.yml", font=SANS, bg=BG3, fg=TEXT2,
                  relief="flat", padx=10, pady=7, cursor="hand2",
                  command=self.view_yml).pack(side="left", padx=4)
        self.status_lbl = tk.Label(run_bar, text="", font=SANS_B, bg=BG, fg=TEXT2)
        self.status_lbl.pack(side="right", padx=10)

        self.output = scrolledtext.ScrolledText(
            right, font=MONO, bg="#0d0d1a", fg=TEXT, relief="flat",
            highlightthickness=1, highlightbackground=BORDER,
            height=12, state="disabled", insertbackground=TEXT)
        self.output.pack(fill="both", expand=False)
        for tag, color in [("pass", GREEN), ("fail", RED), ("info", YELLOW),
                           ("cmd", TEXT2), ("err", RED), ("normal", TEXT)]:
            self.output.tag_config(tag, foreground=color)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _clear_search(self):
        if self.fn_search.get() == "Search...":
            self.fn_search.delete(0, "end")
            self.fn_search.config(fg=TEXT)

    def _restore_search(self):
        if not self.fn_search.get():
            self.fn_search.insert(0, "Search...")
            self.fn_search.config(fg=TEXT2)

    def _filter_fns(self):
        q = self.fn_search.get().lower()
        if q == "search...": q = ""
        self.fn_listbox.delete(0, "end")
        for fn in self.store.functions:
            if q in fn["name"].lower():
                cnt = len(self.store.get_tests(fn["name"]))
                self.fn_listbox.insert(
                    "end", f"  {fn['name']}{f' [{cnt}]' if cnt else ''}")

    def _fn_at(self, idx):
        name = self.fn_listbox.get(idx).strip().split("[")[0].strip()
        return next((f for f in self.store.functions if f["name"] == name), None)

    def open_folder(self):
        folder = filedialog.askdirectory(title="Select C Project Folder")
        if not folder: return
        self.store.set_project(folder)
        self.folder_lbl.config(text=folder[-55:] if len(folder) > 55 else folder)
        self._filter_fns()
        self.log("INFO", f"Loaded: {folder}\nFound {len(self.store.functions)} functions.\n")

    def on_fn_select(self, _=None):
        sel = self.fn_listbox.curselection()
        if not sel: return
        fn = self._fn_at(sel[0])
        if not fn: return
        self.selected_fn = fn
        params = ", ".join(fn["params"]) if fn["params"] else "void"
        self.fn_sig_lbl.config(text=f"{fn['ret']}  {fn['name']}({params})")
        self.fn_file_lbl.config(
            text=os.path.relpath(fn["file"], self.store.project_folder))
        self._load_tests_table()

    def _load_tests_table(self):
        self.tree.delete(*self.tree.get_children())
        if not self.selected_fn: return
        for tc in self.store.get_tests(self.selected_fn["name"]):
            inps  = ", ".join(str(x) for x in tc.get("inputs", []))
            mocks = tc.get("mocks", [])
            mock_s = "; ".join(
                f"{m['fn']}()" for m in mocks if m.get("fn")) or "—"
            self.tree.insert("", "end", values=(
                tc.get("label", ""), inps, tc.get("expected", ""), mock_s))

    def _open_mock_editor(self):
        dlg = MockEditorDialog(self, self._pending_mocks)
        self.wait_window(dlg)
        if dlg.result is not None:
            self._pending_mocks = dlg.result
            n = len(self._pending_mocks)
            self.mock_lbl.config(
                text=(f"{n} expectation{'s' if n!=1 else ''} defined"
                      if n else "No mock expectations"),
                fg=ACCENT2 if n else TEXT2)

    def add_test_case(self):
        if not self.selected_fn:
            messagebox.showwarning("No function", "Select a function first.")
            return
        raw      = self.e_inputs.get().strip()
        inputs   = [x.strip() for x in raw.split(",") if x.strip()] if raw else []
        expected = self.e_expected.get().strip()
        if not expected:
            messagebox.showwarning("Missing", "Expected value is required.")
            return
        self.store.add_test(self.selected_fn["name"], inputs, expected,
                            self.e_label.get().strip(),
                            mocks=self._pending_mocks)
        self._pending_mocks = []
        self.mock_lbl.config(text="No mock expectations", fg=TEXT2)
        self._load_tests_table()
        self._filter_fns()
        self.clear_form()

    def clear_form(self):
        self.e_label.delete(0, "end")
        self.e_inputs.delete(0, "end")
        self.e_expected.delete(0, "end")
        self._pending_mocks = []
        self.mock_lbl.config(text="No mock expectations", fg=TEXT2)

    def delete_selected_test(self):
        sel = self.tree.selection()
        if not sel or not self.selected_fn: return
        self.store.remove_test(self.selected_fn["name"], self.tree.index(sel[0]))
        self._load_tests_table()
        self._filter_fns()

    def _mock_stems(self):
        if not self.selected_fn: return []
        tcs = self.store.get_tests(self.selected_fn["name"])
        return sorted({
            m["header"] for tc in tcs
            for m in tc.get("mocks", []) if m.get("header")})

    def view_generated(self):
        if not self.selected_fn:
            messagebox.showinfo("No function", "Select a function first.")
            return
        fn   = self.selected_fn
        tcs  = self.store.get_tests(fn["name"])
        all_h = [h.name for h in collect_all_headers(self.store.project_folder)]
        code = generate_test_c(fn, tcs, self._mock_stems(), all_h)
        self._show_text(f"test_{fn['name']}.c", code)

    def view_yml(self):
        if not self.selected_fn:
            messagebox.showinfo("No function", "Select a function first.")
            return
        build_dir = Path(self.store.project_folder) / "_ctest_build"
        build_dir.mkdir(exist_ok=True)
        write_project_yml(build_dir, self._mock_stems())
        self._show_text("project.yml", (build_dir / "project.yml").read_text())

    def _show_text(self, title, content):
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("720x540")
        win.configure(bg=BG)
        txt = scrolledtext.ScrolledText(win, font=MONO, bg="#0d0d1a",
                                        fg=TEXT, relief="flat")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("end", content)
        txt.config(state="disabled")

    def run_tests(self):
        if not self.selected_fn:
            messagebox.showwarning("No function", "Select a function first.")
            return
        tcs = self.store.get_tests(self.selected_fn["name"])
        if not tcs:
            messagebox.showwarning("No tests", "Add at least one test case.")
            return
        self.run_btn.config(state="disabled", text="Running...")
        self.output.config(state="normal")
        self.output.delete("1.0", "end")
        self.output.config(state="disabled")
        self.status_lbl.config(text="Running...", fg=YELLOW)
        fn = self.selected_fn
        use_cl = self._use_ceedling.get()
        threading.Thread(
            target=self._run_thread, args=(fn, tcs, use_cl), daemon=True).start()

    def _run_thread(self, fn, tcs, use_ceedling):
        try:
            gen = (run_with_ceedling if use_ceedling else run_with_unity_directly)(
                self.store.project_folder, fn, tcs)
            for kind, text in gen:
                self.after(0, self._append_output, kind, text)
        finally:
            self.after(0, self._run_done)

    def _append_output(self, kind, text):
        self.output.config(state="normal")
        if kind == "RESULT":
            self._colorize_result(text)
        else:
            tag = {"ERROR": "err", "ERR": "err", "CMD": "cmd",
                   "INFO": "info", "OUT": "normal"}.get(kind, "normal")
            self.output.insert("end", text, tag)
        self.output.see("end")
        self.output.config(state="disabled")
        if kind == "RESULT":
            if "FAIL" in text:
                self.status_lbl.config(text="✗ FAILED", fg=RED)
            elif "OK" in text or "PASS" in text:
                self.status_lbl.config(text="✓ PASSED", fg=GREEN)

    def _colorize_result(self, text):
        for line in text.splitlines(keepends=True):
            if ":PASS" in line or line.strip() == "OK":
                self.output.insert("end", line, "pass")
            elif ":FAIL" in line or "FAIL" in line:
                self.output.insert("end", line, "fail")
            elif line.startswith("-") or "Tests" in line:
                self.output.insert("end", line, "info")
            else:
                self.output.insert("end", line, "normal")

    def _run_done(self):
        self.run_btn.config(state="normal", text="▶  Run Tests")

    def log(self, kind, text):
        self.output.config(state="normal")
        tag = {"INFO": "info", "ERROR": "err", "CMD": "cmd"}.get(kind, "normal")
        self.output.insert("end", text, tag)
        self.output.see("end")
        self.output.config(state="disabled")


if __name__ == "__main__":
    App().mainloop()

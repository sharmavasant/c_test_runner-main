"""
C Unit Test Runner — Ceedling + Unity + CMock
Requirements: Python 3.8+, Ruby, Ceedling gem (gem install ceedling)
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os, re, json, subprocess, threading, shutil, sys
from pathlib import Path

# ─── Theme ───────────────────────────────────────────────────────────────────
BG        = "#1e1e2e"
BG2       = "#2a2a3e"
BG3       = "#313145"
ACCENT    = "#7c6af7"
ACCENT2   = "#a78bfa"
GREEN     = "#4ade80"
RED       = "#f87171"
YELLOW    = "#facc15"
TEXT      = "#e2e8f0"
TEXT2     = "#94a3b8"
BORDER    = "#3f3f5c"
MONO      = ("Consolas", 11)
SANS      = ("Segoe UI", 10)
SANS_B    = ("Segoe UI", 10, "bold")
SANS_LG   = ("Segoe UI", 13, "bold")

# ─── C parser ────────────────────────────────────────────────────────────────
FUNC_RE = re.compile(
    r'^\s*'
    r'(?:(?:static|inline|extern|const)\s+)*'
    r'(?P<ret>(?:unsigned\s+)?(?:int|long|short|char|float|double|void|bool|'
    r'uint8_t|uint16_t|uint32_t|int8_t|int16_t|int32_t|size_t|[A-Z_][A-Z0-9_]*)\s*\*?)\s+'
    r'(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s*'
    r'\((?P<params>[^)]*)\)\s*\{',
    re.MULTILINE
)

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
    skip = {"build", "_build", "_ctest_build", "test", "tests", ".git", "vendor", "Debug", "Release"}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith(".c") or f.endswith(".h"):
                fns.extend(parse_functions(os.path.join(root, f)))
    seen = set()
    unique = []
    for fn in fns:
        if fn["name"] not in seen:
            seen.add(fn["name"])
            unique.append(fn)
    return unique

def scan_headers_for_functions(folder):
    # print(folder)
    """Return {header_stem: [fn_name, ...]} for CMock mock generation."""
    result = {}
    skip = {"build", "_build", "_ctest_build", ".git", "vendor", "Debug", "Release"}
    for root, dirs, files in os.walk(folder):
        # print(root, dirs)
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            # print(f)
            if f.endswith(".h"):
                path = Path(root) / f
                fns = parse_functions(path)
                if fns:
                    result[path.stem] = [fn["name"] for fn in fns]
    # print(f"result: {result}")
    return result

# ─── Test data store ─────────────────────────────────────────────────────────
class TestStore:
    def __init__(self):
        self.project_folder = ""
        self.functions      = []
        self.tests          = {}   # {fn_name: [{inputs, expected, label, mocks}]}
        self._save_path     = ""

    def set_project(self, folder):
        self.project_folder = folder
        self.functions      = scan_project(folder)
        self._save_path     = os.path.join(folder, ".ctest_runner.json")
        self._load()

    def _load(self):
        if os.path.exists(self._save_path):
            try:
                data = json.loads(Path(self._save_path).read_text())
                self.tests = data.get("tests", {})
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
            "label":    label or f"test_{len(self.tests[fn_name]) + 1}",
            "mocks":    mocks or [],   # list of {header, fn, args, returns, times}
        })
        self.save()

    def remove_test(self, fn_name, idx):
        if fn_name in self.tests and 0 <= idx < len(self.tests[fn_name]):
            self.tests[fn_name].pop(idx)
            self.save()

    def get_tests(self, fn_name):
        return self.tests.get(fn_name, [])

# ─── CMock / Ceedling helpers ─────────────────────────────────────────────────

def sanitize_label(s):
    return re.sub(r'[^a-zA-Z0-9_]', '_', s)

def _mock_header_name(header_stem):
    """CMock convention: Mock<HeaderName>.h"""
    return f"Mock{header_stem}.h"

def generate_mock_expect(mock):
    """
    Generate a single CMock expectation call from a mock dict:
      {header, fn, args, returns, times, ignore_args}
    Examples:
      uart_send_ExpectAndReturn(data, len, 1);
      sensor_read_ExpectWithArrayAndReturn(buf, 10, 42);
      timer_init_Expect();
    """
    fn       = mock.get("fn", "")
    args     = mock.get("args", "").strip()
    ret      = mock.get("returns", "").strip()
    times    = int(mock.get("times", 1))
    ignore   = mock.get("ignore_args", False)
    lines    = []

    for _ in range(times):
        if ignore:
            if ret:
                call = f"{fn}_IgnoreAndReturn({ret});"
            else:
                call = f"{fn}_Ignore();"
        elif ret:
            arg_part = f"{args}, " if args else ""
            call = f"{fn}_ExpectAndReturn({arg_part}{ret});"
        elif args:
            call = f"{fn}_Expect({args});"
        else:
            call = f"{fn}_Expect();"
        lines.append(call)
    return lines

def generate_test_c(fn_info, test_cases, mock_headers):
    """
    Generate a Unity test .c file with proper CMock includes and expectations.
    mock_headers: list of header stems that need mocking, e.g. ["uart", "sensor"]
    """
    fn  = fn_info["name"]
    ret = fn_info["ret"]
    source_header = Path(fn_info["file"]).with_suffix(".h")

    lines = [
        "/* Auto-generated by C Test Runner */",
        '#include "unity.h"',
    ]

    # Include CMock mock headers
    for hdr in mock_headers:
        lines.append(f'#include "{_mock_header_name(hdr)}"')

    # Include the module under test
    if source_header.exists():
        lines.append(f'#include "{source_header.name}"')
    else:
        params = ", ".join(fn_info["params"]) if fn_info["params"] else "void"
        lines.append(f"/* forward declaration — no header found */")
        lines.append(f"{ret} {fn}({params});")

    lines += ["", "void setUp(void) {}", "void tearDown(void) {}", ""]

    for i, tc in enumerate(test_cases):
        label    = sanitize_label(tc.get("label", f"test_{i+1}"))
        inputs   = tc.get("inputs", [])
        expected = tc.get("expected", "0")
        mocks    = tc.get("mocks", [])
        args     = ", ".join(str(x) for x in inputs)

        lines.append(f"void test_{fn}_{label}(void) {{")

        # Emit CMock expectations
        for mock in mocks:
            for exp_line in generate_mock_expect(mock):
                lines.append(f"    {exp_line}")

        # Actual assertion
        if ret.strip() == "void":
            lines.append(f"    {fn}({args});")
            lines.append(f"    TEST_PASS();")
        elif ret.strip() in ("float", "double"):
            lines.append(f"    TEST_ASSERT_EQUAL_FLOAT({expected}, {fn}({args}));")
        elif ret.strip() in ("char*", "char *", "const char*", "const char *"):
            lines.append(f'    TEST_ASSERT_EQUAL_STRING("{expected}", {fn}({args}));')
        else:
            lines.append(f"    TEST_ASSERT_EQUAL({expected}, {fn}({args}));")

        lines.append("}")
        lines.append("")

    return "\n".join(lines)

# ─── Ceedling project setup ───────────────────────────────────────────────────

# def write_project_yml(build_dir, mock_headers):
#     """
#     Write a project.yml that enables CMock and lists which headers to mock.
#     """
#     mock_paths_yml = ""
#     if mock_headers:
#         mock_paths_yml = "\n  :mock_path:\n    - src/**"

#     mocks_block = ""
#     if mock_headers:
#         mocks_block = "\n:cmock:\n  :mock_prefix: Mock\n  :when_no_prototypes: :warn\n  :enforce_strict_ordering: TRUE\n  :plugins:\n    - :ignore\n    - :ignore_arg\n    - :expect_any_args\n    - :return_thru_ptr\n    - :array\n    - :callback"

#     yml = f""":project:
#   :use_exceptions: FALSE
#   :use_test_preprocessor: :all
#   :build_root: build
#   :release_build: FALSE
#   :test_file_prefix: test_

# :paths:
#   :test:
#     - test/**
#   :source:
#     - src/**
#   :include:
#     - src/**{mock_paths_yml}

# :defines:
#   :test: []
# {mocks_block}

# :plugins:
#   :load_paths:
#     - 'C:/Ruby/lib/ruby/gems/4.0.0/plugins'
#   :enabled:
#     - stdout_pretty_tests_report
#     - module_generator
# """
#     (build_dir / "project.yml").write_text(yml)

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

# def write_project_yml(build_dir, mock_headers):
#     """
#     Write a project.yml that enables CMock and lists which headers to mock.
#     """
#     # 1. Properly format the mock path if headers exist
#     # Note the leading newline and indentation to keep it as a separate YAML key
#     mock_paths_yml = ""
#     if mock_headers:
#         mock_paths_yml = "\n:cmock:\n  :mock_path:\n    - src/**"

#     # 2. CMock configuration block
#     mocks_block = ""
#     if mock_headers:
#         mocks_block = """
#   :mock_prefix: Mock
#   :when_no_prototypes: :warn
#   :enforce_strict_ordering: true
#   :plugins:
#     - :ignore
#     - :ignore_arg
#     - :expect_any_args
#     - :return_thru_ptr
#     - :array
#     - :callback"""

#     # 3. Generate the final YML string
#     # Change: We use a cleaner way to handle the include paths
#     yml = f""":project:
#   :use_exceptions: false
#   :use_test_preprocessor: :all
#   :build_root: build
#   :release_build: false
#   :test_file_prefix: test_

# :paths:
#   :test:
#     - test/**
#   :source:
#     - src/**
#   :include:
#     - src/**
# {mock_paths_yml}

# {":cmock:" if mock_headers else ""}
# {mocks_block if mock_headers else ""}

# :defines:
#   :test: []

# :plugins:
#   :load_paths: 
#     - 'C:/Users/HP/.local/share/gem/ruby/4.0.0/gems/ceedling-1.0.1/plugins'
#   :enabled:
#     - report_tests_pretty_stdout
#     - module_generator
# """
#     (build_dir / "project.yml").write_text(yml)


def setup_ceedling_project(project_folder, fn_info, test_cases):
    """
    Build the full Ceedling project in _ctest_build/:
      src/   — source + headers (including dep headers to mock)
      test/  — generated test file
      project.yml
    Returns (build_dir, test_file_path, mock_headers_used)
    """
    build_dir = Path(project_folder) / "_ctest_build"
    for sub in ("src", "test"):
        (build_dir / sub).mkdir(parents=True, exist_ok=True)

    source_file = Path(fn_info["file"])
    fn_name     = fn_info["name"]
    # print(source_file)
    # Copy source and its header
    shutil.copy2(source_file, build_dir / "src" / source_file.name)
    header = source_file.with_suffix(".h")
    if header.exists():
        print(header)
        shutil.copy2(header, build_dir / "src" / header.name)
    else:
        print(f"header not found {header}")

    # Collect unique mock headers across all test cases
    mock_header_stems = set()
    for tc in test_cases:
        for mock in tc.get("mocks", []):
            h = mock.get("header", "").strip()
            if h:
                mock_header_stems.add(h)

    # Copy dependency headers so CMock can parse them
    all_headers = scan_headers_for_functions(project_folder)
    for stem in mock_header_stems:
        # find the actual .h in the project
        for root, _, files in os.walk(project_folder):
            for f in files:
                if f == f"{stem}.h":
                    src_h = Path(root) / f
                    shutil.copy2(src_h, build_dir / "src" / f)
                    break

    # Generate test file
    test_c   = generate_test_c(fn_info, test_cases, sorted(mock_header_stems))
    test_file = build_dir / "test" / f"test_{fn_name}.c"
    test_file.write_text(test_c, encoding="utf-8")

    # Write project.yml
    write_project_yml(build_dir, mock_header_stems)

    return build_dir, test_file, sorted(mock_header_stems)


def run_with_ceedling(project_folder, fn_info, test_cases):
    """Run tests via Ceedling (uses CMock automatically)."""
    build_dir, test_file, mock_headers = setup_ceedling_project(
        project_folder, fn_info, test_cases)

    fn_name = fn_info["name"]

    yield "INFO", f"Ceedling project written to: {build_dir}\n"
    if mock_headers:
        yield "INFO", f"Mocking headers: {', '.join(mock_headers)}\n"
    yield "INFO", "Running: ceedling test:" + f"test_{fn_name}" + "\n\n"

    ceedling_cmd = shutil.which("ceedling") or "ceedling"
    cmd = [ceedling_cmd, f"test:test_{fn_name}"]

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(build_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        for line in proc.stdout:
            yield "RESULT", line
        proc.wait()
        if proc.returncode not in (0, 1):   # ceedling returns 1 on test failures (normal)
            yield "ERROR", f"\nCeedling exited with code {proc.returncode}\n"
    except FileNotFoundError:
        yield "ERROR", (
            "ceedling not found in PATH.\n"
            "Install it with:  gem install ceedling\n"
            "Then restart this app.\n"
        )


# ─── Fallback: Unity-only (no CMock) ─────────────────────────────────────────

def strip_main(src):
    pattern = re.compile(r'\bint\s+main\s*\([^)]*\)\s*\{', re.MULTILINE)
    m = pattern.search(src)
    if not m:
        return src
    brace_pos = src.index('{', m.start())
    depth = 0
    i = brace_pos
    while i < len(src):
        if src[i] == '{':  depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        i += 1
    else:
        end = len(src)
    lines = src[m.start():end].count('\n')
    placeholder = "/* main() removed by C Test Runner */\n" * max(1, lines)
    return src[:m.start()] + placeholder + src[end:]


def run_with_unity_directly(project_folder, fn_info, test_cases):
    """Compile + run with raw Unity (no Ceedling, no CMock)."""
    build_dir = Path(project_folder) / "_ctest_build"
    build_dir.mkdir(exist_ok=True)

    source_file = Path(fn_info["file"])
    header      = source_file.with_suffix(".h")
    fn_name     = fn_info["name"]

    # Download Unity if missing
    unity_dir = build_dir / "unity"
    unity_c   = unity_dir / "unity.c"
    unity_h   = unity_dir / "unity.h"
    unity_ih  = unity_dir / "unity_internals.h"

    if not unity_c.exists():
        yield "INFO", "Downloading Unity framework...\n"
        unity_dir.mkdir(exist_ok=True)
        try:
            import urllib.request
            base = "https://raw.githubusercontent.com/ThrowTheSwitch/Unity/master/src/"
            for fname, dest in [("unity.c", unity_c), ("unity.h", unity_h),
                                 ("unity_internals.h", unity_ih)]:
                urllib.request.urlretrieve(base + fname, dest)
            yield "INFO", "Unity downloaded.\n"
        except Exception as e:
            yield "ERROR", f"Could not download Unity: {e}\n"
            return

    # Strip main from source
    yield "INFO", f"Preprocessing {source_file.name}...\n"
    orig = source_file.read_text(encoding="utf-8", errors="ignore")
    stripped = strip_main(orig)
    testable_src = build_dir / (source_file.stem + "_testable.c")
    testable_src.write_text(stripped, encoding="utf-8")

    # No CMock available — warn if mocks were requested
    mocks_requested = any(tc.get("mocks") for tc in test_cases)
    if mocks_requested:
        yield "INFO", (
            "WARNING: mock expectations defined but running in Unity-only mode.\n"
            "Install Ceedling (gem install ceedling) for CMock support.\n\n"
        )

    # Generate test file (without mock includes in Unity-only mode)
    if header.exists():
        inc = f'#include "{header.name}"'
    else:
        params = ", ".join(fn_info["params"]) if fn_info["params"] else "void"
        inc = f"{fn_info['ret']} {fn_name}({params});"

    test_c_lines = [
        "/* Auto-generated — Unity only */",
        '#include "unity.h"',
        inc, "",
        "void setUp(void) {}",
        "void tearDown(void) {}", "",
    ]
    for i, tc in enumerate(test_cases):
        label    = sanitize_label(tc.get("label", f"test_{i+1}"))
        inputs   = tc.get("inputs", [])
        expected = tc.get("expected", "0")
        args     = ", ".join(str(x) for x in inputs)
        ret      = fn_info["ret"]
        test_c_lines.append(f"void test_{fn_name}_{label}(void) {{")
        if ret.strip() == "void":
            test_c_lines += [f"    {fn_name}({args});", "    TEST_PASS();"]
        elif ret.strip() in ("float", "double"):
            test_c_lines.append(f"    TEST_ASSERT_EQUAL_FLOAT({expected}, {fn_name}({args}));")
        elif "char*" in ret or "char *" in ret:
            test_c_lines.append(f'    TEST_ASSERT_EQUAL_STRING("{expected}", {fn_name}({args}));')
        else:
            test_c_lines.append(f"    TEST_ASSERT_EQUAL({expected}, {fn_name}({args}));")
        test_c_lines += ["}", ""]

    test_file = build_dir / f"test_{fn_name}.c"
    test_file.write_text("\n".join(test_c_lines), encoding="utf-8")

    # Generate runner
    test_fns = [
        f"test_{fn_name}_{sanitize_label(tc.get('label', f'test_{i+1}'))}"
        for i, tc in enumerate(test_cases)
    ]
    runner_c = build_dir / f"runner_{fn_name}.c"
    rb = '#include "unity.h"\n'
    rb += "".join(f"extern void {f}(void);\n" for f in test_fns)
    rb += "\nint main(void) {\n    UNITY_BEGIN();\n"
    rb += "".join(f"    RUN_TEST({f});\n" for f in test_fns)
    rb += "    return UNITY_END();\n}\n"
    runner_c.write_text(rb, encoding="utf-8")

    # Collect every directory in the project that contains .h files so that
    # transitive includes like "common_include.h" are always found.
    skip_dirs = {"_ctest_build", ".git", "build", "_build"}
    header_dirs: set = {str(unity_dir), str(source_file.parent)}
    for root, dirs, files in os.walk(project_folder):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        if any(f.endswith(".h") for f in files):
            header_dirs.add(root)
    includes = [f"-I{d}" for d in sorted(header_dirs)]

    exe = build_dir / f"test_{fn_name}.exe"
    cmd = [
        "gcc", "-std=c99", "-Wall",
        str(test_file), str(runner_c), str(unity_c), str(testable_src),
        *includes, "-o", str(exe),
    ]

    yield "INFO", f"Include paths ({len(includes)}):\n" + \
        "".join(f"  {p}\n" for p in includes) + "\n"
    yield "CMD", "Compiling:\n  " + " ".join(cmd) + "\n\n"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(build_dir))
        if r.stdout: yield "OUT", r.stdout
        if r.stderr: yield "ERR", r.stderr + "\n"
        if r.returncode != 0:
            yield "ERROR", f"Compilation failed (exit {r.returncode})\n"
            return
        yield "INFO", "Compilation OK. Running tests...\n\n"
        r2 = subprocess.run([str(exe)], capture_output=True, text=True, cwd=str(build_dir))
        if r2.stdout: yield "RESULT", r2.stdout
        if r2.stderr: yield "ERR", r2.stderr
    except FileNotFoundError:
        yield "ERROR", (
            "gcc not found in PATH.\n"
            "Install MinGW-w64 from https://winlibs.com and add bin/ to PATH.\n"
        )

# ─── Mock editor dialog ───────────────────────────────────────────────────────

class MockEditorDialog(tk.Toplevel):
    """
    Pop-up to build a list of CMock expectations for one test case.
    Each row: header | function | args | returns | times | ignore_args
    """
    def __init__(self, parent, existing_mocks=None):
        super().__init__(parent)
        self.title("CMock Expectations Editor")
        self.geometry("860x440")
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
        tk.Label(hdr,
                 text="Each row = one mock call expectation (CMock will verify call order & args)",
                 font=("Segoe UI", 9), bg=BG2, fg=TEXT2).pack(side="left", padx=16)

        # Column headers
        cols_frame = tk.Frame(self, bg=BG3, pady=4)
        cols_frame.pack(fill="x", padx=8)
        for txt, w in [("Header (stem)", 110), ("Function", 130), ("Args", 140),
                       ("Returns", 80), ("Times", 50), ("Ignore args", 80)]:
            tk.Label(cols_frame, text=txt, font=("Segoe UI", 9, "bold"),
                     bg=BG3, fg=TEXT2, width=w//8, anchor="w").pack(side="left", padx=4)

        # Scrollable rows
        canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=8)
        self._rows_frame = tk.Frame(canvas, bg=BG)
        canvas.create_window((0,0), window=self._rows_frame, anchor="nw")
        self._rows_frame.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        for mock in existing:
            self._add_row(mock)

        # Buttons
        btn_row = tk.Frame(self, bg=BG2, pady=8)
        btn_row.pack(fill="x", padx=8)
        tk.Button(btn_row, text="+ Add expectation", font=SANS_B,
                  bg=ACCENT, fg="white", relief="flat", padx=12, pady=4,
                  cursor="hand2", command=self._add_row).pack(side="left")
        tk.Button(btn_row, text="Save", font=SANS_B,
                  bg=GREEN, fg="#0a0a0a", relief="flat", padx=16, pady=4,
                  cursor="hand2", command=self._save).pack(side="right", padx=4)
        tk.Button(btn_row, text="Cancel", font=SANS,
                  bg=BG3, fg=TEXT2, relief="flat", padx=10, pady=4,
                  cursor="hand2", command=self.destroy).pack(side="right", padx=4)

    def _add_row(self, data=None):
        data = data or {}
        row = tk.Frame(self._rows_frame, bg=BG2, pady=3)
        row.pack(fill="x", pady=2)

        def entry(w, val=""):
            e = tk.Entry(row, font=MONO, bg=BG3, fg=TEXT, insertbackground=TEXT,
                         relief="flat", highlightthickness=1, highlightcolor=ACCENT, width=w)
            e.insert(0, val)
            e.pack(side="left", padx=3)
            return e

        e_hdr  = entry(14, data.get("header",""))
        e_fn   = entry(16, data.get("fn",""))
        e_args = entry(18, data.get("args",""))
        e_ret  = entry(10, data.get("returns",""))
        e_times= entry(5,  str(data.get("times",1)))

        ignore_var = tk.BooleanVar(value=data.get("ignore_args", False))
        tk.Checkbutton(row, variable=ignore_var, bg=BG2,
                       activebackground=BG2, fg=TEXT2,
                       selectcolor=BG3).pack(side="left", padx=8)

        def remove():
            row.destroy()
            self._rows[:] = [r for r in self._rows if r[0].winfo_exists()]

        tk.Button(row, text="✕", font=("Segoe UI",9), bg=BG3, fg=RED,
                  relief="flat", cursor="hand2", command=remove).pack(side="left", padx=4)

        self._rows.append((e_hdr, e_fn, e_args, e_ret, e_times, ignore_var))

    def _save(self):
        mocks = []
        for e_hdr, e_fn, e_args, e_ret, e_times, ignore_var in self._rows:
            if not e_hdr.winfo_exists():
                continue
            h  = e_hdr.get().strip()
            fn = e_fn.get().strip()
            if not fn:
                continue
            try:
                times = int(e_times.get().strip())
            except ValueError:
                times = 1
            mocks.append({
                "header":      h,
                "fn":          fn,
                "args":        e_args.get().strip(),
                "returns":     e_ret.get().strip(),
                "times":       times,
                "ignore_args": ignore_var.get(),
            })
        self.result = mocks
        self.destroy()

# ─── Main App ─────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("C Unit Test Runner — Ceedling + Unity + CMock")
        self.geometry("1280x820")
        self.minsize(900, 640)
        self.configure(bg=BG)
        self.store       = TestStore()
        self.selected_fn = None
        self._use_ceedling = tk.BooleanVar(value=True)
        self._build_ui()

    def _build_ui(self):
        # Top bar
        top = tk.Frame(self, bg=BG2, pady=8, padx=14)
        top.pack(fill="x")
        tk.Label(top, text="⬡ C Test Runner", font=("Segoe UI",14,"bold"),
                 bg=BG2, fg=ACCENT2).pack(side="left")

        # Ceedling toggle
        tk.Checkbutton(top, text="Use Ceedling+CMock", variable=self._use_ceedling,
                       font=SANS, bg=BG2, fg=TEXT2, activebackground=BG2,
                       selectcolor=BG3, activeforeground=TEXT
                       ).pack(side="right", padx=12)

        btn_open = tk.Button(top, text="📂  Open Project Folder", font=SANS_B,
                             bg=ACCENT, fg="white", relief="flat", padx=14, pady=5,
                             cursor="hand2", command=self.open_folder)
        btn_open.pack(side="right", padx=4)
        self.folder_lbl = tk.Label(top, text="No project loaded", font=SANS,
                                   bg=BG2, fg=TEXT2)
        self.folder_lbl.pack(side="right", padx=12)

        # Main pane
        pane = tk.PanedWindow(self, orient="horizontal", bg=BG,
                              sashwidth=4, sashrelief="flat")
        pane.pack(fill="both", expand=True, padx=10, pady=(6,10))

        # ── Left: function list ──────────────────────────────────────────────
        left = tk.Frame(pane, bg=BG2)
        pane.add(left, minsize=220, width=260)
        tk.Label(left, text="Functions", font=SANS_B, bg=BG2, fg=TEXT2,
                 pady=8).pack(fill="x", padx=10)
        self.fn_search = tk.Entry(left, font=SANS, bg=BG3, fg=TEXT,
                                  insertbackground=TEXT, relief="flat",
                                  highlightthickness=1, highlightcolor=ACCENT)
        self.fn_search.pack(fill="x", padx=10, pady=(0,6))
        self.fn_search.bind("<KeyRelease>", lambda e: self._filter_fns())
        self.fn_search.insert(0, "Search functions...")
        self.fn_search.config(fg=TEXT2)
        self.fn_search.bind("<FocusIn>",  lambda e: self._clear_search())
        self.fn_search.bind("<FocusOut>", lambda e: self._restore_search())

        fl = tk.Frame(left, bg=BG2)
        fl.pack(fill="both", expand=True, padx=10, pady=(0,10))
        sb = tk.Scrollbar(fl)
        sb.pack(side="right", fill="y")
        self.fn_listbox = tk.Listbox(fl, yscrollcommand=sb.set, font=MONO,
                                     bg=BG3, fg=TEXT, selectbackground=ACCENT,
                                     selectforeground="white", relief="flat",
                                     bd=0, activestyle="none", highlightthickness=0)
        self.fn_listbox.pack(fill="both", expand=True)
        sb.config(command=self.fn_listbox.yview)
        self.fn_listbox.bind("<<ListboxSelect>>", self.on_fn_select)

        # ── Right ────────────────────────────────────────────────────────────
        right = tk.Frame(pane, bg=BG)
        pane.add(right, minsize=500)

        # Signature bar
        self.fn_info_frame = tk.Frame(right, bg=BG2, pady=6, padx=12)
        self.fn_info_frame.pack(fill="x", pady=(0,6))
        self.fn_sig_lbl = tk.Label(self.fn_info_frame, text="← Select a function",
                                   font=MONO, bg=BG2, fg=ACCENT2, anchor="w")
        self.fn_sig_lbl.pack(side="left")
        self.fn_file_lbl = tk.Label(self.fn_info_frame, text="",
                                    font=("Segoe UI",9), bg=BG2, fg=TEXT2)
        self.fn_file_lbl.pack(side="right")

        # ── Add test form ────────────────────────────────────────────────────
        form = tk.LabelFrame(right, text=" Add Test Case ", font=SANS_B,
                             bg=BG2, fg=TEXT2, bd=1, relief="flat",
                             highlightbackground=BORDER, highlightthickness=1)
        form.pack(fill="x", pady=(0,6))

        def frow(parent, label):
            r = tk.Frame(parent, bg=BG2)
            r.pack(fill="x", padx=10, pady=3)
            tk.Label(r, text=label, font=SANS, bg=BG2, fg=TEXT2,
                     width=10, anchor="w").pack(side="left")
            return r

        r1 = frow(form, "Label:")
        self.e_label = tk.Entry(r1, font=MONO, bg=BG3, fg=TEXT,
                                insertbackground=TEXT, relief="flat",
                                highlightthickness=1, highlightcolor=ACCENT)
        self.e_label.pack(side="left", fill="x", expand=True)

        r2 = frow(form, "Inputs:")
        self.e_inputs = tk.Entry(r2, font=MONO, bg=BG3, fg=TEXT,
                                 insertbackground=TEXT, relief="flat",
                                 highlightthickness=1, highlightcolor=ACCENT)
        self.e_inputs.pack(side="left", fill="x", expand=True)
        tk.Label(r2, text="comma-separated", font=("Segoe UI",9),
                 bg=BG2, fg=TEXT2).pack(side="left", padx=8)

        r3 = frow(form, "Expected:")
        self.e_expected = tk.Entry(r3, font=MONO, bg=BG3, fg=TEXT,
                                   insertbackground=TEXT, relief="flat",
                                   highlightthickness=1, highlightcolor=ACCENT)
        self.e_expected.pack(side="left", fill="x", expand=True)
        tk.Label(r3, text="return value", font=("Segoe UI",9),
                 bg=BG2, fg=TEXT2).pack(side="left", padx=8)

        # CMock button row
        r4 = tk.Frame(form, bg=BG2)
        r4.pack(fill="x", padx=10, pady=(4,4))
        self.mock_summary_lbl = tk.Label(r4, text="No mock expectations",
                                          font=("Segoe UI",9), bg=BG2, fg=TEXT2)
        self.mock_summary_lbl.pack(side="left")
        self._pending_mocks = []
        tk.Button(r4, text="🔧 Edit CMock Expectations", font=SANS,
                  bg=BG3, fg=ACCENT2, relief="flat", padx=10, pady=3,
                  cursor="hand2", command=self._open_mock_editor
                  ).pack(side="right")

        btn_row = tk.Frame(form, bg=BG2)
        btn_row.pack(fill="x", padx=10, pady=(4,10))
        tk.Button(btn_row, text="＋ Add Test Case", font=SANS_B,
                  bg=ACCENT, fg="white", relief="flat", padx=16, pady=5,
                  cursor="hand2", command=self.add_test_case).pack(side="left")
        tk.Button(btn_row, text="Clear", font=SANS,
                  bg=BG3, fg=TEXT2, relief="flat", padx=10, pady=5,
                  cursor="hand2", command=self.clear_form).pack(side="left", padx=8)

        # ── Test cases table ─────────────────────────────────────────────────
        tbl = tk.LabelFrame(right, text=" Test Cases ", font=SANS_B,
                            bg=BG2, fg=TEXT2, bd=1, relief="flat",
                            highlightbackground=BORDER, highlightthickness=1)
        tbl.pack(fill="both", expand=True, pady=(0,6))

        cols = ("label","inputs","expected","mocks")
        self.tree = ttk.Treeview(tbl, columns=cols, show="headings",
                                 selectmode="browse", height=6)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=BG3, foreground=TEXT,
                        fieldbackground=BG3, rowheight=26, font=MONO)
        style.configure("Treeview.Heading", background=BG2, foreground=TEXT2,
                        font=SANS_B, relief="flat")
        style.map("Treeview", background=[("selected",ACCENT)],
                  foreground=[("selected","white")])
        for c, w in [("label",140),("inputs",200),("expected",100),("mocks",200)]:
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(tbl, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=4, pady=4)

        del_btn = tk.Button(tbl, text="🗑 Delete Selected", font=SANS,
                            bg=BG3, fg=RED, relief="flat", padx=10, pady=3,
                            cursor="hand2", command=self.delete_selected_test)
        del_btn.pack(anchor="e", padx=8, pady=(0,6))

        # ── Run bar ──────────────────────────────────────────────────────────
        run_bar = tk.Frame(right, bg=BG)
        run_bar.pack(fill="x", pady=(0,4))
        self.run_btn = tk.Button(run_bar, text="▶  Run Tests", font=SANS_B,
                                 bg=GREEN, fg="#0a0a0a", relief="flat",
                                 padx=20, pady=7, cursor="hand2",
                                 command=self.run_tests)
        self.run_btn.pack(side="left")
        tk.Button(run_bar, text="📄  View .c", font=SANS,
                  bg=BG3, fg=TEXT2, relief="flat", padx=10, pady=7,
                  cursor="hand2", command=self.view_generated).pack(side="left", padx=8)
        tk.Button(run_bar, text="📋  Copy project.yml", font=SANS,
                  bg=BG3, fg=TEXT2, relief="flat", padx=10, pady=7,
                  cursor="hand2", command=self.view_project_yml).pack(side="left", padx=4)
        self.status_lbl = tk.Label(run_bar, text="", font=SANS_B, bg=BG, fg=TEXT2)
        self.status_lbl.pack(side="right", padx=10)

        self.output = scrolledtext.ScrolledText(
            right, font=MONO, bg="#0d0d1a", fg=TEXT, relief="flat",
            highlightthickness=1, highlightbackground=BORDER,
            height=12, state="disabled", insertbackground=TEXT)
        self.output.pack(fill="both", expand=False)
        self.output.tag_config("pass",   foreground=GREEN)
        self.output.tag_config("fail",   foreground=RED)
        self.output.tag_config("info",   foreground=YELLOW)
        self.output.tag_config("cmd",    foreground=TEXT2)
        self.output.tag_config("err",    foreground=RED)
        self.output.tag_config("normal", foreground=TEXT)

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _clear_search(self):
        if self.fn_search.get() == "Search functions...":
            self.fn_search.delete(0, "end")
            self.fn_search.config(fg=TEXT)

    def _restore_search(self):
        if not self.fn_search.get():
            self.fn_search.insert(0, "Search functions...")
            self.fn_search.config(fg=TEXT2)

    def _filter_fns(self):
        q = self.fn_search.get().lower()
        if q == "search functions...": q = ""
        self.fn_listbox.delete(0, "end")
        for fn in self.store.functions:
            if q in fn["name"].lower():
                cnt = len(self.store.get_tests(fn["name"]))
                badge = f" [{cnt}]" if cnt else ""
                self.fn_listbox.insert("end", f"  {fn['name']}{badge}")

    def _fn_at(self, idx):
        text = self.fn_listbox.get(idx).strip().split("[")[0].strip()
        for fn in self.store.functions:
            if fn["name"] == text:
                return fn
        return None

    def open_folder(self):
        folder = filedialog.askdirectory(title="Select C Project Folder")
        if not folder: return
        self.store.set_project(folder)
        self.folder_lbl.config(
            text=folder[-55:] if len(folder) > 55 else folder)
        self._filter_fns()
        self.log("INFO", f"Loaded: {folder}\n")
        self.log("INFO", f"Found {len(self.store.functions)} functions.\n")

    def on_fn_select(self, _=None):
        sel = self.fn_listbox.curselection()
        if not sel: return
        fn = self._fn_at(sel[0])
        if not fn: return
        self.selected_fn = fn
        params = ", ".join(fn["params"]) if fn["params"] else "void"
        self.fn_sig_lbl.config(text=f"{fn['ret']}  {fn['name']}({params})")
        rel = os.path.relpath(fn["file"], self.store.project_folder)
        self.fn_file_lbl.config(text=rel)
        self._load_tests_table()

    def _load_tests_table(self):
        self.tree.delete(*self.tree.get_children())
        if not self.selected_fn: return
        for tc in self.store.get_tests(self.selected_fn["name"]):
            inps   = ", ".join(str(x) for x in tc.get("inputs", []))
            mocks  = tc.get("mocks", [])
            mock_s = "; ".join(
                f"{m['fn']}()" for m in mocks if m.get("fn")) or "—"
            self.tree.insert("", "end", values=(
                tc.get("label",""), inps, tc.get("expected",""), mock_s))

    def _open_mock_editor(self):
        dlg = MockEditorDialog(self, self._pending_mocks)
        self.wait_window(dlg)
        if dlg.result is not None:
            self._pending_mocks = dlg.result
            n = len(self._pending_mocks)
            self.mock_summary_lbl.config(
                text=f"{n} expectation{'s' if n!=1 else ''} defined" if n
                     else "No mock expectations",
                fg=ACCENT2 if n else TEXT2)

    def add_test_case(self):
        if not self.selected_fn:
            messagebox.showwarning("No function", "Select a function first.")
            return
        raw = self.e_inputs.get().strip()
        inputs   = [x.strip() for x in raw.split(",") if x.strip()] if raw else []
        expected = self.e_expected.get().strip()
        label    = self.e_label.get().strip()
        if not expected:
            messagebox.showwarning("Missing", "Expected value is required.")
            return
        self.store.add_test(
            self.selected_fn["name"], inputs, expected, label,
            mocks=self._pending_mocks)
        self._pending_mocks = []
        self.mock_summary_lbl.config(text="No mock expectations", fg=TEXT2)
        self._load_tests_table()
        self._filter_fns()
        self.clear_form()

    def clear_form(self):
        self.e_label.delete(0, "end")
        self.e_inputs.delete(0, "end")
        self.e_expected.delete(0, "end")
        self._pending_mocks = []
        self.mock_summary_lbl.config(text="No mock expectations", fg=TEXT2)

    def delete_selected_test(self):
        sel = self.tree.selection()
        if not sel or not self.selected_fn: return
        idx = self.tree.index(sel[0])
        self.store.remove_test(self.selected_fn["name"], idx)
        self._load_tests_table()
        self._filter_fns()

    def view_generated(self):
        if not self.selected_fn:
            messagebox.showinfo("No function", "Select a function first.")
            return
        fn  = self.selected_fn
        tcs = self.store.get_tests(fn["name"])
        mock_headers = sorted({
            m["header"] for tc in tcs for m in tc.get("mocks", []) if m.get("header")})
        code = generate_test_c(fn, tcs, mock_headers)
        win = tk.Toplevel(self)
        win.title(f"test_{fn['name']}.c")
        win.geometry("720x540")
        win.configure(bg=BG)
        txt = scrolledtext.ScrolledText(win, font=MONO, bg="#0d0d1a", fg=TEXT, relief="flat")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("end", code)
        txt.config(state="disabled")

    def view_project_yml(self):
        if not self.selected_fn:
            messagebox.showinfo("No function", "Select a function first.")
            return
        tcs = self.store.get_tests(self.selected_fn["name"])
        mock_headers = sorted({
            m["header"] for tc in tcs for m in tc.get("mocks", []) if m.get("header")})
        build_dir = Path(self.store.project_folder) / "_ctest_build"
        build_dir.mkdir(exist_ok=True)
        write_project_yml(build_dir, mock_headers)
        yml_text = (build_dir / "project.yml").read_text()
        win = tk.Toplevel(self)
        win.title("project.yml")
        win.geometry("640x480")
        win.configure(bg=BG)
        txt = scrolledtext.ScrolledText(win, font=MONO, bg="#0d0d1a", fg=TEXT, relief="flat")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("end", yml_text)
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
        use_ceedling = self._use_ceedling.get()
        t = threading.Thread(
            target=self._run_thread, args=(fn, tcs, use_ceedling), daemon=True)
        t.start()

    def _run_thread(self, fn, tcs, use_ceedling):
        try:
            if use_ceedling:
                gen = run_with_ceedling(self.store.project_folder, fn, tcs)
            else:
                gen = run_with_unity_directly(self.store.project_folder, fn, tcs)
            for kind, text in gen:
                self.after(0, self._append_output, kind, text)
        finally:
            self.after(0, self._run_done)

    def _append_output(self, kind, text):
        self.output.config(state="normal")
        if kind == "RESULT":
            self._colorize_result(text)
        else:
            tag = {"ERROR":"err","ERR":"err","CMD":"cmd",
                   "INFO":"info","OUT":"normal"}.get(kind, "normal")
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
        tag = {"INFO":"info","ERROR":"err","CMD":"cmd"}.get(kind,"normal")
        self.output.insert("end", text, tag)
        self.output.see("end")
        self.output.config(state="disabled")


if __name__ == "__main__":
    app = App()
    app.mainloop()

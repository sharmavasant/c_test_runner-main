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

# Typedef block — prepended to project .h and .c files only
_TYPEDEF_BLOCK = """#ifndef _CTEST_RUNNER_TYPES_
#define _CTEST_RUNNER_TYPES_
typedef signed   char       int8_t;
typedef signed   short      int16_t;
typedef signed   int        int32_t;
typedef signed   long long  int64_t;
typedef unsigned char       uint8_t;
typedef unsigned short      uint16_t;
typedef unsigned int        uint32_t;
typedef unsigned long long  uint64_t;
#endif
"""

# Standard C library header names that must NEVER be stubbed or patched —
# gcc must find the real system versions of these.
_SYSTEM_HEADERS = {
    "stdint.h", "stdbool.h", "stddef.h", "stdarg.h", "stdlib.h",
    "stdio.h", "string.h", "math.h", "assert.h", "limits.h",
    "float.h", "time.h", "ctype.h", "errno.h", "signal.h",
    "setjmp.h", "locale.h", "wchar.h", "unity.h", "unity_internals.h",
}


# Global set of relative paths that are real project files (populated in setup)
_REAL_PROJECT_HEADERS: set = set()


def inject_typedefs_into_src(src_dir):
    """No-op — we never modify project files. Types are handled via ctest_types.h."""
    return 0


def inject_typedefs_into_src_single(file_path):
    """No-op — we never modify project files."""
    pass


def resolve_missing_includes(src_dir):
    """No stubs — real project files are used as-is via -I flags."""
    return 0


def find_declaring_header(fn_name, project_folder):
    """
    Search every .h in the project for a declaration of fn_name.
    Returns the filename (basename only) of the first match, or None.
    """
    decl_re = re.compile(
        r'\b' + re.escape(fn_name) + r'\s*\(',
        re.MULTILINE)
    skip = {"_ctest_build", "build", "_build", ".git", "vendor"}
    for root, dirs, files in os.walk(project_folder):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if not f.endswith(".h"):
                continue
            try:
                text = (Path(root) / f).read_text(encoding="utf-8", errors="ignore")
                if decl_re.search(text):
                    return f
            except Exception:
                pass
    return None


def _clean_type(t):
    """Strip storage class keywords from a type string."""
    for kw in ("static", "extern", "register", "volatile"):
        t = re.sub(r'\b' + kw + r'\b\s*', '', t)
    return t.strip()


def _extract_type_names(fn_info):
    """
    Extract all non-primitive type names used in a function's signature
    (return type + parameters). Returns a set of names like {'ip_addr_t'}.
    """
    # Primitive types we already define or that are built-in
    primitives = {
        "void", "int", "char", "short", "long", "float", "double",
        "unsigned", "signed", "const", "struct", "enum", "union",
        "uint8_t", "uint16_t", "uint32_t", "uint64_t",
        "int8_t", "int16_t", "int32_t", "int64_t",
        "bool", "size_t", "NULL",
    }
    custom = set()
    # Collect all type tokens from ret + params
    all_text = fn_info.get("ret", "") + " " + " ".join(fn_info.get("params", []))
    for token in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', all_text):
        if token not in primitives:
            custom.add(token)
    return custom


def _resolve_typedefs(type_names, project_folder):
    """
    Search all .h files in the project for typedef definitions of the
    given type names.  Follows chains: if ip_addr_t is typedef'd from
    ip4_addr_t, we also find ip4_addr_t's definition, and so on.

    Returns a list of typedef lines in dependency order (safe to paste
    directly into a .c file).
    """
    skip = {"_ctest_build", "build", "_build", ".git", "vendor"}

    # Collect every typedef line from every header in the project
    # Pattern matches: typedef ... name; and typedef struct { } name;
    typedef_re = re.compile(
        r'typedef\b[^;]+?\b([A-Za-z_][A-Za-z0-9_]*)\s*;',
        re.DOTALL)

    # Map: type_name -> full typedef text
    all_typedefs = {}
    for root, dirs, files in os.walk(project_folder):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if not f.endswith(".h"):
                continue
            try:
                text = (Path(root) / f).read_text(encoding="utf-8", errors="ignore")
                # Strip comments first
                text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
                text = re.sub(r'//[^\n]*', '', text)
                for m in typedef_re.finditer(text):
                    name = m.group(1)
                    full = m.group(0).strip()
                    if name not in all_typedefs:
                        all_typedefs[name] = full
            except Exception:
                pass

    # BFS: resolve type_names + their dependencies
    resolved_names = set()
    resolved_lines = []
    queue = list(type_names)

    while queue:
        name = queue.pop(0)
        if name in resolved_names:
            continue
        resolved_names.add(name)
        if name not in all_typedefs:
            continue
        typedef_text = all_typedefs[name]
        # Avoid double semicolons — typedef text may already end with ;
        td_clean = typedef_text.rstrip(";").strip()
        resolved_lines.append(td_clean + ";")
        # Find any new type names this typedef references
        for token in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', typedef_text):
            if token not in resolved_names and token != name:
                queue.append(token)

    return resolved_lines


def generate_test_c(fn_info, test_cases, mock_header_stems,
                    declaring_header=None, extra_typedefs=None,
                    source_file_name=None):
    """
    Generate a self-contained test file with NO project header includes.
    Uses a forward declaration of the function + inlined typedefs for any
    custom types in the signature (e.g. ip_addr_t, ip4_addr_t).
    """
    fn     = fn_info["name"]
    ret    = _clean_type(fn_info["ret"])
    params = fn_info["params"]
    param_str = ", ".join(params) if params else "void"

    lines = [
        "/* Auto-generated by C Test Runner */",
        '#include "ctest_types.h"  /* defines uint8_t, uint32_t etc */',
        "",
    ]

    # Inline any custom typedefs resolved from the project headers
    if extra_typedefs:
        lines.append("/* === Custom types from project === */")
        lines.append("#ifndef _CTEST_CUSTOM_TYPES_")
        lines.append("#define _CTEST_CUSTOM_TYPES_")
        for td in extra_typedefs:
            lines.append(td)
        lines.append("#endif")
        lines.append("")

    lines.append('#include "unity.h"')
    lines.append("")

    for stem in mock_header_stems:
        lines.append(f'#include "Mock{stem}.h"')

    # TEST_SOURCE_FILE tells Ceedling exactly which .c to compile and link.
    # We point it to the extracted single-function file to avoid compiling
    # unrelated functions that depend on unavailable SDK types.
    src_file = source_file_name if source_file_name else Path(fn_info["file"]).name
    lines.append(f'/* Ceedling: compile only the extracted function file */')
    lines.append(f'TEST_SOURCE_FILE("{src_file}")')
    lines.append("")
    lines.append("/* Forward declaration of function under test */")
    lines.append(f"{ret} {fn}({param_str});")
    lines.append("")
    lines += ["void setUp(void) {}", "void tearDown(void) {}", ""]

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
def write_project_yml(build_dir, mock_header_stems):
    """
    Write project.yml + a gcc response file containing all -I paths.

    The TI SDK has hundreds of subdirectories. Passing each as a separate
    -I flag overflows the OS command-line limit (~8KB on Windows).
    Solution: write every -I to includes.rsp and pass @includes.rsp as
    a single flag — gcc reads the file and expands it internally.
    """
    src_dir = build_dir / "src"

    # Build response file with all -I paths
    rsp_lines = []
    rsp_lines.append('-Isrc')
    for d in sorted(src_dir.rglob("*")):
        if d.is_dir():
            rel = str(d.relative_to(build_dir)).replace("\\", "/")
            rsp_lines.append(f"-I{rel}")
    rsp_path = build_dir / "includes.rsp"
    rsp_path.write_text("\n".join(rsp_lines), encoding="utf-8")

    cmock_block = ""
    if mock_header_stems:
        cmock_block = """
:cmock:
  :mock_prefix: Mock
  :when_no_prototypes: :warn
  :enforce_strict_ordering: TRUE
  :plugins:
    - :ignore
    - :ignore_arg
    - :expect_any_args
    - :return_thru_ptr
    - :array
    - :callback
"""
    rsp_abs = "C:/Users/user/Desktop/unit/mcspi_loopback_interrupt_lld_am263x-cc_r5fss0-0_nortos_ti-arm-clang/_ctest_build/includes.rsp"#str(rsp_path).replace("\\", "/")

    yml = f""":project:
  :use_exceptions: FALSE
  :use_test_preprocessor: :none
  :build_root: build
  :release_build: FALSE
  :test_file_prefix: test_

:paths:
  :test:
    - test/**
  :source:
    - src/**
  :include:
    - src
{cmock_block}
:flags:
  :test:
    :compile:
      :*:
        - -w
        - "{rsp_abs}"
    :preprocess:
      :*:
        - -w
        - "{rsp_abs}"

:plugins:
  :load_paths:
    - 'C:/Users/user/.local/share/gem/ruby/4.0.0/gems/ceedling-1.0.1/plugins'
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


# ctest_types.h content — written into src/ and included first in extracted file
_CTEST_TYPES_H = """/* ctest_types.h — auto-generated by C Test Runner */
/* Included FIRST in the extracted function file so types are always defined */
#ifndef _CTEST_RUNNER_TYPES_
#define _CTEST_RUNNER_TYPES_
typedef signed   char       int8_t;
typedef signed   short      int16_t;
typedef signed   int        int32_t;
typedef signed   long long  int64_t;
typedef unsigned char       uint8_t;
typedef unsigned short      uint16_t;
typedef unsigned int        uint32_t;
typedef unsigned long long  uint64_t;
#if defined(__STDC_VERSION__) && __STDC_VERSION__ < 202311L
  #ifndef bool
    typedef unsigned char bool;
    #define true  1
    #define false 0
  #endif
#endif
#endif /* _CTEST_RUNNER_TYPES_ */
"""


def extract_function(source_file, fn_name, src_dir):
    """
    Extract just the target function from source_file into a minimal .c.
    The extracted file:
      1. Includes ctest_types.h FIRST (defines uint8_t etc.)
      2. Includes all original #include lines from the source file
      3. Contains ONLY the target function body — no other functions

    This avoids compiling sibling functions that use unavailable SDK types.
    Project files are never modified.
    """
    # Write ctest_types.h into src/ so it can be found
    (src_dir / "ctest_types.h").write_text(_CTEST_TYPES_H, encoding="utf-8")

    try:
        text = Path(source_file).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    # Collect all #include lines from original file
    includes = []
    for line in text.splitlines():
        if line.strip().startswith("#include"):
            includes.append(line)

    # Find the function body using brace counting
    fn_re = re.compile(
        r'(?:^|\n)([^\n]*\b' + re.escape(fn_name) + r'\s*\([^)]*\)\s*\{)',
        re.MULTILINE)
    m = fn_re.search(text)
    if not m:
        return None

    start     = m.start(1)
    brace_pos = text.index('{', start)
    depth = 0
    i = brace_pos
    while i < len(text):
        if text[i] == '{':   depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
        i += 1
    else:
        end = len(text)

    fn_body = text[start:end]

    # Remove macro-only calls that expand to SDK-dependent code (e.g. my_custom_line()).
    # These are calls with no arguments that match a known pattern — they are
    # instrumentation macros not needed for unit testing.
    fn_body = re.sub(r'\b[a-zA-Z_][a-zA-Z0-9_]*\(\)\s*;\s*\n', '', fn_body)

    # Standard headers always needed — add them unconditionally
    std_includes = [
        '#include <math.h>',
        '#include <string.h>',
    ]

    result  = "/* Extracted by C Test Runner — only target function */\n"
    result += '#include "ctest_types.h"\n'
    result += "\n".join(std_includes) + "\n"
    result += "\n"
    result += fn_body + "\n"
    return result


def setup_ceedling_project(project_folder, fn_info, test_cases, sdk_path=""):
    """
    Prepare _ctest_build/ for Ceedling.

    src/ mirrors the ENTIRE project folder structure — every .c and .h
    is copied preserving its relative path.  This means:
      - #include <lwip/ip_addr.h> finds the real src/lwip/ip_addr.h
      - #include "common_include.h" finds the real file
      - No flat-copy collisions, no stubs overwriting real headers
      - All typedefs (ip_addr_t etc.) are present in their real headers

    Only truly missing SDK headers (not present anywhere in the project)
    get stubbed out.
    """
    build_dir = Path(project_folder) / "_ctest_build"

    # Always wipe and recreate — ensures no stale stubs or old files persist
    if build_dir.exists():
        shutil.rmtree(build_dir)
    src_dir = build_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "test").mkdir(parents=True, exist_ok=True)

    fn_name   = fn_info["name"]
    proj_path = Path(project_folder)

    copied_c, stripped_mains = [], []
    _REAL_PROJECT_HEADERS.clear()

    def copy_tree(source_root, dest_root_in_src, base_for_rel, skip_dirs):
        """
        Copy all .c and .h files from source_root into dest_root_in_src,
        preserving the directory structure relative to base_for_rel.
        .c files get main() stripped. .h files are copied exactly as-is.
        """
        source_root = Path(source_root)
        base_for_rel = Path(base_for_rel)
        for root, dirs, files in os.walk(source_root):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            rel_root  = Path(root).relative_to(base_for_rel)
            dest_root = dest_root_in_src / rel_root
            dest_root.mkdir(parents=True, exist_ok=True)
            for fname in files:
                src_file  = Path(root) / fname
                dest_file = dest_root / fname
                if fname.endswith(".c"):
                    try:
                        text = src_file.read_text(encoding="utf-8", errors="ignore")
                        clean, had_main = strip_main_from_text(text)
                        dest_file.write_text(clean, encoding="utf-8")
                        copied_c.append(fname)
                        if had_main:
                            stripped_mains.append(fname)
                    except Exception:
                        pass
                elif fname.endswith(".h"):
                    if fname in _SYSTEM_HEADERS:
                        continue
                    try:
                        shutil.copy2(src_file, dest_file)
                        rel_h = str(rel_root / fname).replace("\\", "/")
                        _REAL_PROJECT_HEADERS.add(fname)
                        _REAL_PROJECT_HEADERS.add(rel_h)
                    except Exception:
                        pass

    # 1. Copy entire project folder (source .c and .h files)
    skip = {"_ctest_build", "build", "_build", ".git", "vendor"}
    copy_tree(project_folder, src_dir, project_folder, skip)

    # 2. Copy TI SDK source folder so <drivers/uart.h> etc. resolve
    #    to real files with original content instead of being missing.
    if sdk_path and Path(sdk_path).exists():
        copy_tree(sdk_path, src_dir, sdk_path, set())

    # Collect mock stems from test cases
    mock_header_stems = sorted({
        m["header"].strip()
        for tc in test_cases
        for m in tc.get("mocks", [])
        if m.get("header", "").strip()
    })

    # ── Iteratively stub out every missing include (handles deep chains) ─────
    # e.g.  utility.h -> common_include.h -> ti_drivers_config.h
    #        -> <drivers/hw_include/cslr_soc.h>  (SDK, not on disk) -> stub
    src_dir = build_dir / "src"
    n_stubs = resolve_missing_includes(src_dir)

    # Prepend typedefs to every .h and .c in src/ (skip stdint/stdbool)
    n_injected = inject_typedefs_into_src(src_dir)

    # Resolve custom types used in the function signature (e.g. ip_addr_t)
    custom_type_names = _extract_type_names(fn_info)
    extra_typedefs    = _resolve_typedefs(custom_type_names, project_folder) \
                        if custom_type_names else []

    # Extract ONLY the target function into a minimal .c file.
    # This avoids compiling all other functions in utility.c that depend
    # on unavailable SDK types (ip_addr_t, I2CLLD_Message, etc.)
    extracted = extract_function(fn_info["file"], fn_name, src_dir)
    extracted_name = f"{fn_name}_extracted.c"
    extracted_dest = src_dir / extracted_name
    if extracted:
        extracted_dest.write_text(extracted, encoding="utf-8")
    else:
        # Fallback: use the full source file name
        extracted_name = Path(fn_info["file"]).name

    # Write test file — forward declaration + inlined typedefs, no project headers
    test_c = generate_test_c(fn_info, test_cases, mock_header_stems,
                             extra_typedefs=extra_typedefs,
                             source_file_name=extracted_name)
    test_file = build_dir / "test" / f"test_{fn_name}.c"
    test_file.write_text(test_c, encoding="utf-8")

    # Write project.yml
    write_project_yml(build_dir, mock_header_stems)

    return build_dir, test_file, mock_header_stems, copied_c, stripped_mains, n_stubs


def run_with_ceedling(project_folder, fn_info, test_cases, sdk_path=""):
    build_dir, test_file, mock_headers, copied_c, stripped, n_stubs = setup_ceedling_project(
        project_folder, fn_info, test_cases, sdk_path=sdk_path)
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
        self._sdk_path      = tk.StringVar(
            value=r"C:\ti\mcu_plus_sdk_am263x_10_02_00_13\source")
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

        # SDK path bar
        sdk_bar = tk.Frame(self, bg=BG3, pady=4, padx=14)
        sdk_bar.pack(fill="x")
        tk.Label(sdk_bar, text="TI SDK source:", font=SANS, bg=BG3,
                 fg=TEXT2).pack(side="left")
        sdk_entry = tk.Entry(sdk_bar, textvariable=self._sdk_path,
                             font=MONO, bg=BG2, fg=TEXT, insertbackground=TEXT,
                             relief="flat", highlightthickness=1,
                             highlightcolor=ACCENT, width=60)
        sdk_entry.pack(side="left", padx=8, fill="x", expand=True)
        tk.Button(sdk_bar, text="Browse", font=SANS, bg=ACCENT, fg="white",
                  relief="flat", padx=8, pady=2, cursor="hand2",
                  command=self._browse_sdk).pack(side="left")

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

    def _browse_sdk(self):
        folder = filedialog.askdirectory(title="Select TI SDK source/ folder")
        if folder:
            self._sdk_path.set(folder)

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
        custom = _extract_type_names(fn)
        extra  = _resolve_typedefs(custom, self.store.project_folder) if custom else []
        code   = generate_test_c(fn, tcs, self._mock_stems(), extra_typedefs=extra)
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
        use_cl  = self._use_ceedling.get()
        sdk_path = self._sdk_path.get().strip()
        threading.Thread(
            target=self._run_thread, args=(fn, tcs, use_cl, sdk_path),
            daemon=True).start()

    def _run_thread(self, fn, tcs, use_ceedling, sdk_path=""):
        try:
            if use_ceedling:
                gen = run_with_ceedling(self.store.project_folder, fn, tcs,
                                        sdk_path=sdk_path)
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

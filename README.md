# C Unit Test Runner — Unity + Ceedling
A desktop GUI for Windows to add and run unit tests on your existing C project functions.

---

## Quick Start (Windows)

1. **Double-click `setup_windows.bat`** — it checks dependencies and launches the app.

   Or run manually:
   ```
   python test_runner.py
   ```

---

## Requirements

| Tool | Purpose | Install |
|------|---------|---------|
| Python 3.8+ | Runs the GUI | https://python.org |
| gcc (MinGW-w64) | Compiles C tests | https://winlibs.com |
| (Optional) Ruby + Ceedling | Full Ceedling workflow | `gem install ceedling` |

> **tkinter** is included with Python by default — no extra install needed.

### Install gcc (MinGW-w64) — recommended

1. Go to https://winlibs.com
2. Download the latest **GCC for Windows x86_64** (UCRT runtime) `.zip`
3. Extract to `C:\mingw64`
4. Add `C:\mingw64\bin` to your **System PATH**
5. Open a new terminal and run `gcc --version` to confirm

---

## How to Use

### 1. Open your project
Click **"Open Project Folder"** and select the root of your C project.
The app automatically scans all `.c` and `.h` files and extracts all function signatures.

### 2. Select a function
Click any function in the left panel. Its signature and source file are shown at the top.

### 3. Add test cases
Fill in the form:
- **Label** — a short name for this test (e.g. `add_positive`, `edge_zero`)
- **Inputs** — comma-separated argument values (e.g. `3, 5` or `"hello"`)
- **Expected** — the expected return value
- **Mock** (optional) — CMock setup calls if your function has dependencies

Click **＋ Add Test Case**.

### 4. Run tests
Click **▶ Run Tests**.

The app will:
1. Auto-download Unity (first run only) into `<your_project>/_ctest_build/unity/`
2. Generate a `test_<function>.c` file
3. Compile with `gcc`
4. Run the binary and display color-coded results

### 5. View generated test file
Click **"View Generated .c"** to inspect the Unity test source before running.

---

## Output Colors

| Color | Meaning |
|-------|---------|
| 🟢 Green | Test PASSED |
| 🔴 Red | Test FAILED or compile error |
| 🟡 Yellow | Info / status |
| Gray | Compile command |

---

## Test data persistence
Test cases are saved automatically to `<your_project>/.ctest_runner.json` so they persist between sessions.

---

## Example

For a function like:
```c
int add(int a, int b) {
    return a + b;
}
```

Add test cases:
| Label | Inputs | Expected |
|-------|--------|----------|
| basic | 1, 2 | 3 |
| zero | 0, 0 | 0 |
| negative | -3, 3 | 0 |

---

## Troubleshooting

**"gcc not found"** — Install MinGW-w64 and add `C:\mingw64\bin` to PATH.

**"Unity download failed"** — Download unity.c / unity.h / unity_internals.h manually from:
https://github.com/ThrowTheSwitch/Unity/tree/master/src
and place them in `<your_project>/_ctest_build/unity/`

**Function not appearing in list** — Make sure the function body (with `{`) is in a `.c` file, not just declared in a header.

**Compile error about missing header** — The app looks for a `.h` file with the same name as your `.c` file. If your header has a different name, add `#include "yourheader.h"` in the mock field temporarily.

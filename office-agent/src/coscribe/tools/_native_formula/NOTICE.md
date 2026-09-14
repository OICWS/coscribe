# Vendored LaTeX -> OMML compiler

`formula_ast.py`, `formula_parser.py`, `formula_profile.py`, `formula_omml.py`,
`formula_compiler.py` are copied, unmodified, from
[`hugohe3/ppt-master`](https://github.com/hugohe3/ppt-master), commit
`6e3ce9c5a3b994a0e223a14a0f7eddf42fd0b9f5` (2026-09-12), at
`skills/ppt-master/scripts/svg_to_pptx/native_objects/`.

Chosen from that project's much larger codebase specifically because these
five files are genuinely self-contained -- each module's own docstring
states "Dependencies: None (only uses standard library and local PPT
Master modules)", verified by reading the actual import graph before
vendoring: `formula_compiler.py`'s two public functions
(`compile_latex_to_omml`/`compile_latex_to_inline_omml`) only reach into
these five files, never into ppt-master's own "workspace/IR" project
system that the rest of its codebase is built around. `formula.py`/
`inline_formula.py` (ppt-master's own callers of this compiler) were
deliberately *not* vendored -- they import `drawingml.context`/
`drawingml.utils`, i.e. they are workspace-coupled; coscribe's own
`tools/presentations.py` (`add_pptx_formula`) does that gluing itself,
using its own existing python-pptx shape/run conventions instead.

## License

MIT License

Copyright (c) 2025-2026 Hugo He

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

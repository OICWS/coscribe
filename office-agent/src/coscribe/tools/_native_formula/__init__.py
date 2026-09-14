"""Vendored LaTeX -> OMML (Office Math Markup Language) compiler -- see
``NOTICE.md`` in this directory for exact provenance. Only two functions
from here are meant to be called from outside this package:
``formula_compiler.compile_latex_to_omml``/``compile_latex_to_inline_omml``
(re-exported below); everything else is this compiler's own internal AST/
parser/serializer plumbing.
"""

from __future__ import annotations

from .formula_compiler import (
    FormulaCompileError,
    compile_latex_to_inline_omml,
    compile_latex_to_omml,
)

__all__ = [
    "FormulaCompileError",
    "compile_latex_to_inline_omml",
    "compile_latex_to_omml",
]

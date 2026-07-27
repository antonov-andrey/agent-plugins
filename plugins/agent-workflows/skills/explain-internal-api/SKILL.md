---
name: explain-internal-api
description: Declare current internal classes, methods, functions, signatures, and owned fields.
---

# Explain Internal API

## Goal
Render the requested current code scope as declaration blocks equivalent to a `Classes And Methods` section.

## Rules
- Follow the `agent-workflows` plugin support owner `lib/explain-skill/protocol.md`.
- Output declarations, not prose summaries.
- Include public and important owner-local classes, methods, and standalone functions that define workflow behavior, boundary behavior, stable state, state transitions, persistence, external interactions, or contract-relevant transformations.
- Exclude trivial helpers unless they own a real decision, transformation, boundary, state transition, or side effect.
- Include stable field declarations for documented field-like classes when those fields are part of the current internal API surface.
- For each documented class, include the class declaration and every documented owned method declaration with current decorators, parameter names, type annotations, keyword-only markers, default values, and return annotation.
- For each documented standalone function, include the full declaration with current decorators when applicable, parameter names, type annotations, keyword-only markers, default values, and return annotation.
- Use one-line docstrings inside class, method, and standalone function declarations to state each documented symbol's responsibility.
- Use `pass` for every declaration body.

## Output Shape
- Then output grouped `###` functional-role headings.
- Under each `###` heading, output exactly one fenced `python` declaration block.
- Put every documented class and standalone function for that functional role inside that one code block.
- Do not add prose bullets, prose paragraphs, or responsibility lists for individual symbols outside the declaration blocks.

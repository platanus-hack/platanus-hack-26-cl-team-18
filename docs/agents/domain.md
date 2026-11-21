# Domain docs

One `CONTEXT.md` at the repo root holds the domain glossary (it already
exists; extend it when a term is new or contested). The product is described
in `docs/product.md`, and the runtime design in `docs/architecture.md`. If the code grows
several bounded contexts (`app/modules/*`), add a `CONTEXT-MAP.md` at the root
and a `CONTEXT.md` beside each context's code.

Architectural decisions go in `docs/adr/` (`0001-slug.md`; 0001–0003 exist).
Architecture rules are in `docs/rules.md`; kernel usage is in
`docs/common_patterns.md` and `app/modules/common/README.md`.

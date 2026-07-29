"""Camada de domínio (core).

Concentra as regras de negócio: entidades, serviços de orquestração,
interfaces de provedores externos, exceções tipadas e utilitários
puros. Esta camada NUNCA importa PySide6 — essa restrição é
verificável por `grep -r "PySide6" app/core`.
"""
"""Modelo de domínio `PromptPreset` (Prompt 4).

Representa um preset de prompt usado na geração de imagens. É uma
estrutura imutável de dados — nada de UI nem de SQL aqui. A
persistência é responsabilidade de `app.data.storage.prompt_preset_store`.

Os campos `model`, `resolution`, `quality`, `output_format`, `background`
e `n_variations` ficam como opcionais sem valores default ainda: a
documentação concreta do SDK da OpenAI é referenciada no Prompt 6, e
inventar nomes aqui seria assumir uma API sem garantia.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Optional


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    # `datetime.utcnow()` está deprecado em 3.12+; usamos `now(timezone.utc)`
    # para evitar警告 em builds novos.
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PromptPreset:
    """Preset de prompt persistido.

    Attributes:
        id: UUID v4 como string.
        name: nome visível ao usuário.
        description: descrição opcional.
        prompt_text: texto completo do prompt. Sem limite artificial
            de tamanho — testes gravam 5000+ chars sem truncar.
        created_at: momento de criação (UTC, tz-aware).
        updated_at: momento da última atualização (UTC, tz-aware).
        is_default: True se este é o preset padrão do sistema.
            Apenas UM preset pode ter `is_default=True` ao mesmo
            tempo — o `PromptPresetStore` garante a invariante.

    Campos opcionais (regra 10 do prompt — preparados para o Prompt 6
    e seguintes). Não implementados neste prompt:
        model: nome do modelo (ex: "gpt-image-1"). None = default.
        resolution: tupla (largura, altura) ou None.
        quality: string livre (depende da API).
        output_format: "png", "jpeg", "webp" — depende da API.
        background: "transparent", "opaque" — depende da API.
        n_variations: número de variações por imagem.
    """

    id: str = field(default_factory=_new_uuid)
    name: str = ""
    description: str = ""
    prompt_text: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    is_default: bool = False

    # Parâmetros futuros (Prompt 6+). Mantidos None por padrão.
    model: Optional[str] = None
    resolution: Optional[tuple[int, int]] = None
    quality: Optional[str] = None
    output_format: Optional[str] = None
    background: Optional[str] = None
    n_variations: Optional[int] = None

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    def prompt_hash(self) -> str:
        """SHA-256 do `prompt_text` calculado sob demanda.

        Este é o hash que será usado pelo `ImageJob` no Prompt 8
        para compor a chave de idempotência. Não persistimos esse
        valor no preset — ele é função pura do `prompt_text`.
        """
        import hashlib

        return hashlib.sha256(self.prompt_text.encode("utf-8")).hexdigest()

    def with_updates(
        self,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        prompt_text: Optional[str] = None,
        is_default: Optional[bool] = None,
        model: Optional[str] = None,
        resolution: Optional[tuple[int, int]] = None,
        quality: Optional[str] = None,
        output_format: Optional[str] = None,
        background: Optional[str] = None,
        n_variations: Optional[int] = None,
    ) -> "PromptPreset":
        """Devolve uma nova instância com os campos alterados.

        `updated_at` é tocado para o momento atual. `id`, `created_at`
        e `is_default` (quando não fornecido) permanecem.
        """
        return replace(
            self,
            name=name if name is not None else self.name,
            description=description if description is not None else self.description,
            prompt_text=prompt_text if prompt_text is not None else self.prompt_text,
            is_default=is_default if is_default is not None else self.is_default,
            model=model if model is not None else self.model,
            resolution=resolution if resolution is not None else self.resolution,
            quality=quality if quality is not None else self.quality,
            output_format=(
                output_format if output_format is not None else self.output_format
            ),
            background=background if background is not None else self.background,
            n_variations=(
                n_variations if n_variations is not None else self.n_variations
            ),
            updated_at=_utcnow(),
        )


__all__ = ["PromptPreset"]


# Texto do preset padrão de fábrica. Mantido em constante do modelo
# para que tanto o store quanto o teste consigam referenciar
# exatamente o mesmo conteúdo.
DEFAULT_FACTORY_NAME = "Fotografia comercial de vestuário — padrão"
DEFAULT_FACTORY_DESCRIPTION = (
    "Preset padrão de fábrica. Boa linha de base para fotografia de "
    "produto de e-commerce: fundo neutro, iluminação de estúdio, "
    "foco no produto."
)
DEFAULT_FACTORY_PROMPT = (
    "Crie uma arte de catálogo profissional utilizando a camiseta da foto enviada. "
    "Substitua completamente a camiseta do modelo pela camiseta DryFit azul-turquesa "
    "da imagem do usuário, mantendo fielmente sua cor, logo Nike preto bordado no peito "
    "esquerdo e modelagem. Use exatamente o mesmo layout da referência DryFit: fundo preto, "
    "foto principal à esquerda, coluna à direita com 'FRENTE', 'COSTAS' e 'DETALHE DO TECIDO'. "
    "O modelo deve ser masculino, atlético, usando a camiseta vestida naturalmente "
    "(não segurando). Na parte inferior mantenha o painel de informações com: "
    "TAMANHO: GG, COR: AZUL TURQUESA, TECIDO: DRYFIT (POLIÉSTER), MODELAGEM: REGULAR. "
    "Mantenha também os ícones 'TECIDO RESPIRÁVEL', 'SECAGEM RÁPIDA' e "
    "'LEVE E CONFORTÁVEL'. O foco deve ser totalmente na camiseta e o resultado deve "
    "parecer um catálogo profissional para e-commerce."
)

#: Model default do app quando o `PromptPreset.model` for `None`
#: ou string vazia. Casa com o docstring do campo `'None = default'`.
#: Manter centralizado aqui para que tanto o store quanto a UI
#: possam referenciar o mesmo fallback.
DEFAULT_MODEL = "gpt-image-1"

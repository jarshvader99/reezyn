"""Language packs: the voices and phrasing Reezyn speaks in.

A pack owns everything Reezyn contributes to a line: the voice catalog and
Kokoro language, sentence and word rules, filler connectors, tool-action
phrases, and the short joins used to build a sentence. The agent's own words
are never translated; a pack only decides how Reezyn talks around them.

Adding a language means adding one pack here and giving it a voice group. The
narration pipeline, menu, and speaker all resolve their behavior from the
selected voice, so nothing else needs a language branch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LanguagePack:
    code: str
    name: str
    kokoro_lang: str
    voices: dict[str, str]
    connectors: tuple[str, ...]
    connector_starts: tuple[str, ...]
    sentence_split: re.Pattern[str]
    word: re.Pattern[str]
    phrases: dict[str, str]
    link_word: str
    and_word: str
    arrow_word: str
    so_word: str
    equals_word: str
    percent_word: str
    command_word: str
    that_word: str
    file_word: str
    reasoning_labels: re.Pattern[str]

    def phrase(self, key: str, **values: str) -> str:
        return self.phrases[key].format(**values)


ENGLISH_PHRASES = {
    "reading_file": "reading {file}",
    "reading_code": "reading through the code",
    "updating_file": "updating {file}",
    "editing_files": "editing the files",
    "writing_file": "writing {file}",
    "writing_a_file": "writing a file",
    "deleting_file": "deleting {file}",
    "deleting_a_file": "deleting a file",
    "notebook": "updating the notebook",
    "searching_for": 'searching for "{pattern}"',
    "searching_codebase": "searching the codebase",
    "finding_files": "finding the relevant files",
    "checking_docs": "checking the docs",
    "reading_docs": "reading the docs",
    "subagent": "handing that to a subagent",
    "plan": "updating the plan",
    "screenshot": "looking at the screenshot",
    "skill": "loading a skill",
    "question": "asking a question",
    "followups": "offering next steps",
    "lsp": "checking the language server",
    "rules": "checking the project rules",
    "memory": "updating its notes",
    "using_mcp": "using {server}",
    "using_tool": "using {tool}",
    "mcp_server": "an MCP server",
    "tool_noun": "tool",
    "working": "working",
    "running_tests": "running the tests",
    "checking_code": "checking the code",
    "checking_changes": "checking what changed",
    "recent_history": "looking at the recent history",
    "installing_dependencies": "installing dependencies",
    "looking_around": "looking around the project",
    "organizing_files": "organizing the files",
    "checking_endpoint": "checking an endpoint",
    "working_with_git": "working with git",
    "running_project": "running the project",
    "running_command": "running a command",
    "action_single": "I'm {action}.",
    "action_pair": "I'm {first}, then {second}.",
    "action_suffix": " I'm {action}.",
}

SPANISH_PHRASES = {
    "reading_file": "leyendo {file}",
    "reading_code": "leyendo el código",
    "updating_file": "actualizando {file}",
    "editing_files": "editando los archivos",
    "writing_file": "escribiendo {file}",
    "writing_a_file": "escribiendo un archivo",
    "deleting_file": "borrando {file}",
    "deleting_a_file": "borrando un archivo",
    "notebook": "actualizando el cuaderno",
    "searching_for": 'buscando "{pattern}"',
    "searching_codebase": "buscando en el código",
    "finding_files": "buscando los archivos relevantes",
    "checking_docs": "revisando la documentación",
    "reading_docs": "leyendo la documentación",
    "subagent": "pasando esto a un subagente",
    "plan": "actualizando el plan",
    "screenshot": "mirando la captura de pantalla",
    "skill": "cargando una habilidad",
    "question": "haciendo una pregunta",
    "followups": "ofreciendo los siguientes pasos",
    "lsp": "consultando el servidor de lenguaje",
    "rules": "revisando las reglas del proyecto",
    "memory": "actualizando sus notas",
    "using_mcp": "usando {server}",
    "using_tool": "usando {tool}",
    "mcp_server": "un servidor MCP",
    "tool_noun": "herramienta",
    "working": "trabajando",
    "running_tests": "ejecutando las pruebas",
    "checking_code": "revisando el código",
    "checking_changes": "viendo qué cambió",
    "recent_history": "revisando el historial reciente",
    "installing_dependencies": "instalando dependencias",
    "looking_around": "explorando el proyecto",
    "organizing_files": "organizando los archivos",
    "checking_endpoint": "comprobando un endpoint",
    "working_with_git": "trabajando con git",
    "running_project": "ejecutando el proyecto",
    "running_command": "ejecutando un comando",
    "action_single": "Estoy {action}.",
    "action_pair": "Estoy {first}, y luego {second}.",
    "action_suffix": " Estoy {action}.",
}

FRENCH_PHRASES = {
    "reading_file": "lire {file}",
    "reading_code": "lire le code",
    "updating_file": "mettre à jour {file}",
    "editing_files": "modifier les fichiers",
    "writing_file": "écrire {file}",
    "writing_a_file": "écrire un fichier",
    "deleting_file": "supprimer {file}",
    "deleting_a_file": "supprimer un fichier",
    "notebook": "mettre à jour le carnet",
    "searching_for": "chercher « {pattern} »",
    "searching_codebase": "chercher dans le code",
    "finding_files": "trouver les fichiers pertinents",
    "checking_docs": "consulter la documentation",
    "reading_docs": "lire la documentation",
    "subagent": "confier ça à un sous-agent",
    "plan": "mettre à jour le plan",
    "screenshot": "regarder la capture d'écran",
    "skill": "charger une compétence",
    "question": "poser une question",
    "followups": "proposer les prochaines étapes",
    "lsp": "consulter le serveur de langage",
    "rules": "vérifier les règles du projet",
    "memory": "mettre à jour ses notes",
    "using_mcp": "utiliser {server}",
    "using_tool": "utiliser {tool}",
    "mcp_server": "un serveur MCP",
    "tool_noun": "l'outil",
    "working": "travailler",
    "running_tests": "lancer les tests",
    "checking_code": "vérifier le code",
    "checking_changes": "voir ce qui a changé",
    "recent_history": "regarder l'historique récent",
    "installing_dependencies": "installer les dépendances",
    "looking_around": "explorer le projet",
    "organizing_files": "organiser les fichiers",
    "checking_endpoint": "vérifier une API",
    "working_with_git": "travailler avec git",
    "running_project": "lancer le projet",
    "running_command": "lancer une commande",
    "action_single": "Je vais {action}.",
    "action_pair": "Je vais {first}, puis {second}.",
    "action_suffix": " Je vais {action}.",
}

ENGLISH = LanguagePack(
    code="en",
    name="English",
    kokoro_lang="en-us",
    voices={
        "af_heart": "Heart",
        "am_michael": "Michael",
        "af_bella": "Bella",
        "bm_george": "George",
        "bf_emma": "Emma",
    },
    connectors=("Okay,", "Right,", "So,", "Now,"),
    connector_starts=("okay", "right", "so ", "now ", "well", "hmm"),
    sentence_split=re.compile(r"(?<=[.!?])\s+"),
    word=re.compile(r"[A-Za-z0-9']+"),
    phrases=ENGLISH_PHRASES,
    link_word="a link",
    and_word=" and ",
    arrow_word=" to ",
    so_word=" so ",
    equals_word=" equals ",
    percent_word=" percent ",
    command_word="the command",
    that_word="that",
    file_word="the file",
    reasoning_labels=re.compile(
        r"^(?:thinking|reasoning|analysis|thought)\s*[:\-]\s*", re.IGNORECASE
    ),
)

SPANISH = LanguagePack(
    code="es",
    name="Spanish",
    kokoro_lang="es",
    voices={
        "ef_dora": "Dora",
        "em_alex": "Alex",
        "em_santa": "Santa",
    },
    connectors=("Bueno,", "Vale,", "Entonces,", "Ahora,"),
    connector_starts=("bueno", "vale", "entonces", "ahora", "pues", "mmm"),
    sentence_split=re.compile(r"(?<=[.!?…])\s+"),
    word=re.compile(r"[^\W_]+"),
    phrases=SPANISH_PHRASES,
    link_word="un enlace",
    and_word=" y ",
    arrow_word=" a ",
    so_word=" así que ",
    equals_word=" igual a ",
    percent_word=" por ciento ",
    command_word="el comando",
    that_word="eso",
    file_word="el archivo",
    reasoning_labels=re.compile(
        r"^(?:thinking|reasoning|analysis|thought|pensando|razonamiento|análisis|reflexión)"
        r"\s*[:\-]\s*",
        re.IGNORECASE,
    ),
)

FRENCH = LanguagePack(
    code="fr",
    name="French",
    kokoro_lang="fr-fr",
    voices={"ff_siwis": "Siwis"},
    connectors=("Bon,", "Alors,", "Donc,", "Voilà,"),
    connector_starts=("bon", "alors", "donc", "voilà", "voila", "bref", "euh", "heu"),
    sentence_split=re.compile(r"(?<=[.!?…])\s+"),
    word=re.compile(r"[^\W_]+(?:['’][^\W_]+)*"),
    phrases=FRENCH_PHRASES,
    link_word="un lien",
    and_word=" et ",
    arrow_word=" vers ",
    so_word=" donc ",
    equals_word=" égal à ",
    percent_word=" pour cent ",
    command_word="la commande",
    that_word="ça",
    file_word="le fichier",
    reasoning_labels=re.compile(
        r"^(?:thinking|reasoning|analysis|thought|raisonnement|réflexion|pensée|analyse)"
        r"\s*[:\-]\s*",
        re.IGNORECASE,
    ),
)

LANGUAGES: tuple[LanguagePack, ...] = (ENGLISH, SPANISH, FRENCH)
PACKS: dict[str, LanguagePack] = {pack.code: pack for pack in LANGUAGES}
DEFAULT_PACK = ENGLISH
DEFAULT_VOICE = "af_heart"

VOICES: dict[str, str] = {
    voice: label for pack in LANGUAGES for voice, label in pack.voices.items()
}
VOICE_PACKS: dict[str, LanguagePack] = {
    voice: pack for pack in LANGUAGES for voice in pack.voices
}


def pack_for_voice(voice: str) -> LanguagePack:
    """The pack a voice belongs to; unknown voices keep today's behavior."""

    return VOICE_PACKS.get(voice, DEFAULT_PACK)


def pack_for_code(code: str) -> LanguagePack:
    return PACKS.get(code, DEFAULT_PACK)

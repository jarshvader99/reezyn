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
    phrases: dict[str, str | tuple[str, ...]]
    banter: tuple[str, ...]
    # Lines chosen instead of the generic set when the silence follows a
    # particular kind of work: "tests", "command", or "reading".
    scenario_banter: dict[str, tuple[str, ...]]
    # Spoken forms for what technical text is full of.
    units: dict[str, str]
    abbreviations: dict[str, str]
    version_word: str
    range_word: str
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

    def phrase(self, key: str, variant: int = 0, **values: str) -> str:
        """A phrase, optionally picking one of a key's alternate wordings."""

        text = self.phrases[key]
        if isinstance(text, tuple):
            text = text[variant % len(text)]
        return text.format(**values)

    def banter_lines(self, scenario: str = "") -> tuple[str, ...]:
        """Lull lines for what the agent was last doing, or the generic set."""

        return self.scenario_banter.get(scenario) or self.banter


ENGLISH_PHRASES = {
    "reading_file": ("reading {file}", "opening {file}", "looking at {file}"),
    "reading_code": (
        "reading through the code",
        "going through the code",
        "reading the source",
    ),
    "updating_file": ("updating {file}", "editing {file}", "changing {file}"),
    "editing_files": (
        "editing the files",
        "making the edits",
        "updating the files",
    ),
    "writing_file": ("writing {file}", "creating {file}", "putting together {file}"),
    "writing_a_file": "writing a file",
    "deleting_file": ("deleting {file}", "removing {file}", "cleaning up {file}"),
    "deleting_a_file": "deleting a file",
    "notebook": "updating the notebook",
    "searching_for": (
        'searching for "{pattern}"',
        'looking for "{pattern}"',
        'finding "{pattern}"',
    ),
    "searching_codebase": (
        "searching the codebase",
        "looking through the code",
        "scanning the project",
    ),
    "finding_files": (
        "finding the relevant files",
        "locating the files",
        "looking for the files",
    ),
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
    "running_tests": (
        "running the tests",
        "running the test suite",
        "checking the tests",
    ),
    "checking_code": "checking the code",
    "checking_changes": (
        "checking what changed",
        "looking at the diff",
        "seeing what changed",
    ),
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
    "ago_session": "Earlier in this session",
    "ago_today": "Earlier today",
    "ago_past": "On a previous day",
    "recall_thought": "{when}, the agent concluded: {gist}.",
    "recall_action": "{when}, the agent was {action}.",
    "recap_asked": "You asked: {text}.",
    "recap_decided": "It concluded: {text}.",
    "recap_last": "Last action: {action}.",
    "recap_empty": "Nothing to recap in this project yet.",
    "send_prompt": "Ready to send it? Say send, or keep going.",
    "op_ne": "not equal to",
    "op_le": "less than or equal to",
    "op_ge": "greater than or equal to",
    "cue_open": (
        "Let's see.",
        "Okay, let's look.",
        "Right, let's get into it.",
        "Alright, let's take a look.",
    ),
    "cue_return": (
        "Back to this.",
        "Right, back to it.",
        "Okay, where were we.",
    ),
    "cue_close": (
        "That's done.",
        "That's it for this one.",
        "Okay, that's finished.",
    ),
}

SPANISH_PHRASES = {
    "reading_file": ("leyendo {file}", "abriendo {file}", "revisando {file}"),
    "reading_code": (
        "leyendo el código",
        "repasando el código",
        "revisando el código",
    ),
    "updating_file": ("actualizando {file}", "editando {file}", "cambiando {file}"),
    "editing_files": (
        "editando los archivos",
        "haciendo los cambios",
        "actualizando los archivos",
    ),
    "writing_file": ("escribiendo {file}", "creando {file}", "preparando {file}"),
    "writing_a_file": "escribiendo un archivo",
    "deleting_file": ("borrando {file}", "eliminando {file}", "quitando {file}"),
    "deleting_a_file": "borrando un archivo",
    "notebook": "actualizando el cuaderno",
    "searching_for": (
        'buscando "{pattern}"',
        'mirando "{pattern}"',
        'localizando "{pattern}"',
    ),
    "searching_codebase": (
        "buscando en el código",
        "revisando el código",
        "rastreando el proyecto",
    ),
    "finding_files": (
        "buscando los archivos relevantes",
        "localizando los archivos",
        "encontrando los archivos",
    ),
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
    "running_tests": (
        "ejecutando las pruebas",
        "lanzando las pruebas",
        "corriendo las pruebas",
    ),
    "checking_code": "revisando el código",
    "checking_changes": (
        "viendo qué cambió",
        "mirando el diff",
        "revisando los cambios",
    ),
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
    "ago_session": "Antes en esta sesión",
    "ago_today": "Hoy más temprano",
    "ago_past": "En un día anterior",
    "recall_thought": "{when}, el agente concluyó: {gist}.",
    "recall_action": "{when}, el agente estaba {action}.",
    "recap_asked": "Pediste: {text}.",
    "recap_decided": "Concluyó: {text}.",
    "recap_last": "Última acción: {action}.",
    "recap_empty": "Todavía no hay nada que resumir en este proyecto.",
    "send_prompt": "¿Lo envío? Di enviar, o sigue hablando.",
    "op_ne": "distinto de",
    "op_le": "menor o igual que",
    "op_ge": "mayor o igual que",
    "cue_open": (
        "Vamos a ver.",
        "Vale, veamos.",
        "Bien, vamos allá.",
        "De acuerdo, veamos.",
    ),
    "cue_return": (
        "Volviendo a esto.",
        "Vale, seguimos.",
        "A ver, ¿dónde estábamos?",
    ),
    "cue_close": (
        "Listo.",
        "Eso es todo por ahora.",
        "Vale, terminado.",
    ),
}

FRENCH_PHRASES = {
    "reading_file": ("lire {file}", "ouvrir {file}", "regarder {file}"),
    "reading_code": ("lire le code", "parcourir le code", "examiner le code"),
    "updating_file": ("mettre à jour {file}", "modifier {file}", "changer {file}"),
    "editing_files": (
        "modifier les fichiers",
        "faire les modifications",
        "mettre à jour les fichiers",
    ),
    "writing_file": ("écrire {file}", "créer {file}", "préparer {file}"),
    "writing_a_file": "écrire un fichier",
    "deleting_file": ("supprimer {file}", "retirer {file}", "nettoyer {file}"),
    "deleting_a_file": "supprimer un fichier",
    "notebook": "mettre à jour le carnet",
    "searching_for": (
        "chercher « {pattern} »",
        "trouver « {pattern} »",
        "repérer « {pattern} »",
    ),
    "searching_codebase": (
        "chercher dans le code",
        "parcourir le code",
        "explorer le projet",
    ),
    "finding_files": (
        "trouver les fichiers pertinents",
        "localiser les fichiers",
        "chercher les fichiers",
    ),
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
    "running_tests": ("lancer les tests", "exécuter les tests", "vérifier les tests"),
    "checking_code": "vérifier le code",
    "checking_changes": ("voir ce qui a changé", "regarder le diff", "vérifier les changements"),
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
    "ago_session": "Plus tôt dans cette session",
    "ago_today": "Plus tôt aujourd'hui",
    "ago_past": "Un jour précédent",
    "recall_thought": "{when}, l'agent a conclu : {gist}.",
    "recall_action": "{when}, l'agent était en train de {action}.",
    "recap_asked": "Vous avez demandé : {text}.",
    "recap_decided": "Il a conclu : {text}.",
    "recap_last": "Dernière action : {action}.",
    "recap_empty": "Rien à résumer dans ce projet pour l'instant.",
    "send_prompt": "Je l'envoie ? Dites envoyer, ou continuez.",
    "op_ne": "différent de",
    "op_le": "inférieur ou égal à",
    "op_ge": "supérieur ou égal à",
    "cue_open": (
        "Voyons voir.",
        "Bon, regardons.",
        "Alors, allons-y.",
        "Bien, voyons ça.",
    ),
    "cue_return": (
        "Je reviens à ça.",
        "Bon, on reprend.",
        "Alors, où en étions-nous ?",
    ),
    "cue_close": (
        "C'est fait.",
        "Voilà, terminé.",
        "Bon, c'est bon.",
    ),
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
    connectors=("Okay,", "Right,", "So,", "Now,", "Alright,", "Well,"),
    connector_starts=(
        "okay",
        "right",
        "so ",
        "now ",
        "well",
        "alright",
        "hmm",
    ),
    sentence_split=re.compile(r"(?<=[.!?])\s+"),
    word=re.compile(r"[A-Za-z0-9']+"),
    phrases=ENGLISH_PHRASES,
    banter=(
        "Still working. These longer steps can take a minute.",
        "No news yet. I'm listening for the next one.",
        "Quiet stretch. A long tool call usually means a lot is happening.",
        "Still here. Letting this one run.",
        "Nothing to report yet, which is often a good sign.",
        "Taking a while. I'll keep the room calm until it lands.",
    ),
    scenario_banter={
        "tests": (
            "The suite is still running. Test runs are often the quiet part.",
            "Still on the tests. A failing run can take a while to report.",
        ),
        "command": (
            "The command is still going. I'll wait for it to land.",
            "Still running that. Long commands do not report until they finish.",
        ),
        "reading": (
            "Still reading. I'll speak up when there is something worth saying.",
            "Working through the files. Nothing to add yet.",
        ),
    },
    units={
        "ms": "milliseconds",
        "s": "seconds",
        "sec": "seconds",
        "secs": "seconds",
        "m": "minutes",
        "min": "minutes",
        "mins": "minutes",
        "h": "hours",
        "hr": "hours",
        "hrs": "hours",
        "kb": "kilobytes",
        "mb": "megabytes",
        "gb": "gigabytes",
        "tb": "terabytes",
        "px": "pixels",
        "fps": "frames per second",
        "x": "times",
        "k": "thousand",
    },
    abbreviations={
        "e.g.": "for example",
        "i.e.": "that is",
        "etc.": "and so on.",
        "vs.": "versus",
        "approx.": "about",
        "w/o": "without",
        "w/": "with",
    },
    version_word="version",
    range_word="to",
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
    connectors=("Bueno,", "Vale,", "Entonces,", "Ahora,", "Pues,", "Bien,"),
    connector_starts=(
        "bueno",
        "vale",
        "entonces",
        "ahora",
        "pues",
        "bien",
        "mmm",
    ),
    sentence_split=re.compile(r"(?<=[.!?…])\s+"),
    word=re.compile(r"[^\W_]+"),
    phrases=SPANISH_PHRASES,
    banter=(
        "Sigue trabajando. Estos pasos largos a veces tardan un minuto.",
        "Todavía no hay novedades. Sigo escuchando.",
        "Un rato de silencio. Una llamada larga suele significar que pasa mucho.",
        "Aquí sigo. Dejo que termine.",
        "Nada que contar por ahora, y eso suele ser buena señal.",
        "Está tardando. Mantengo el ambiente tranquilo hasta que llegue.",
    ),
    scenario_banter={
        "tests": (
            "Las pruebas siguen corriendo. Suelen ser la parte silenciosa.",
            "Todavía en las pruebas. Una ejecución puede tardar en avisar.",
        ),
        "command": (
            "El comando sigue en marcha. Espero a que termine.",
            "Sigue ejecutándose. Los comandos largos no avisan hasta el final.",
        ),
        "reading": (
            "Sigo leyendo. Hablaré cuando haya algo que merezca la pena.",
            "Repasando los archivos. Todavía nada que contar.",
        ),
    },
    units={
        "ms": "milisegundos",
        "s": "segundos",
        "sec": "segundos",
        "secs": "segundos",
        "m": "minutos",
        "min": "minutos",
        "mins": "minutos",
        "h": "horas",
        "hr": "horas",
        "hrs": "horas",
        "kb": "kilobytes",
        "mb": "megabytes",
        "gb": "gigabytes",
        "tb": "terabytes",
        "px": "píxeles",
        "fps": "fotogramas por segundo",
        "x": "veces",
        "k": "mil",
    },
    abbreviations={
        "p. ej.": "por ejemplo",
        "p.ej.": "por ejemplo",
        "ej.": "por ejemplo",
        "etc.": "etcétera.",
        "vs.": "frente a",
        "aprox.": "aproximadamente",
        "s/": "sin",
        "c/": "con",
    },
    version_word="versión",
    range_word="a",
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
    connectors=("Bon,", "Alors,", "Donc,", "Voilà,", "Bien,", "Bref,"),
    connector_starts=(
        "bon",
        "alors",
        "donc",
        "voilà",
        "voila",
        "bien",
        "bref",
        "euh",
        "heu",
    ),
    sentence_split=re.compile(r"(?<=[.!?…])\s+"),
    word=re.compile(r"[^\W_]+(?:['’][^\W_]+)*"),
    phrases=FRENCH_PHRASES,
    banter=(
        "Ça travaille toujours. Ces étapes peuvent prendre un moment.",
        "Pas de nouvelles pour l'instant. Je reste à l'écoute.",
        "Un moment de calme. Un appel long veut souvent dire qu'il se passe beaucoup de choses.",
        "Je suis toujours là. Je laisse tourner.",
        "Rien à signaler pour l'instant, et c'est souvent bon signe.",
        "Ça prend un peu de temps. Je garde l'ambiance tranquille.",
    ),
    scenario_banter={
        "tests": (
            "Les tests tournent encore. C'est souvent la partie silencieuse.",
            "Toujours sur les tests. Une exécution peut prendre un moment.",
        ),
        "command": (
            "La commande tourne encore. J'attends qu'elle rende la main.",
            "Ça s'exécute toujours. Les commandes longues ne disent rien avant la fin.",
        ),
        "reading": (
            "Je lis encore. Je parlerai quand il y aura quelque chose à dire.",
            "Je parcours les fichiers. Rien à signaler pour l'instant.",
        ),
    },
    units={
        "ms": "millisecondes",
        "s": "secondes",
        "sec": "secondes",
        "secs": "secondes",
        "m": "minutes",
        "min": "minutes",
        "mins": "minutes",
        "h": "heures",
        "hr": "heures",
        "hrs": "heures",
        "kb": "kilooctets",
        "mb": "mégaoctets",
        "gb": "gigaoctets",
        "tb": "téraoctets",
        "px": "pixels",
        "fps": "images par seconde",
        "x": "fois",
        "k": "mille",
    },
    abbreviations={
        "p. ex.": "par exemple",
        "p.ex.": "par exemple",
        "ex.": "par exemple",
        "etc.": "et cetera.",
        "vs.": "contre",
        "env.": "environ",
    },
    version_word="version",
    range_word="à",
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

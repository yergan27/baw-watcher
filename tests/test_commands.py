"""
Tests del bot de comandos de Telegram.

No tocamos la red: probamos `_handle_update` directo, con `_reply`
parcheado para capturar lo que el bot respondería.
"""
from commands import TelegramCommandBot


def _make_bot(replies, handlers=None):
    handlers = handlers if handlers is not None else {
        "estado": lambda: "ESTADO OK",
    }
    bot = TelegramCommandBot(
        token="token-falso", allowed_chat_ids=[123], handlers=handlers)
    bot._reply = lambda chat_id, text: replies.append((chat_id, text))
    return bot


def _update(text, chat_id=123, update_id=1):
    return {"update_id": update_id,
            "message": {"chat": {"id": chat_id}, "text": text}}


def test_comando_conocido_invoca_su_handler():
    replies = []
    _make_bot(replies)._handle_update(_update("/estado"))
    assert replies == [("123", "ESTADO OK")]


def test_comando_con_arroba_del_bot_funciona():
    # Telegram manda "/estado@MiBot" en grupos.
    replies = []
    _make_bot(replies)._handle_update(_update("/estado@Peppy_alertas_bot"))
    assert replies[0][1] == "ESTADO OK"


def test_comando_desconocido_devuelve_ayuda():
    replies = []
    _make_bot(replies)._handle_update(_update("/loquesea"))
    assert len(replies) == 1
    assert "comandos disponibles" in replies[0][1].lower()


def test_chat_no_autorizado_se_ignora():
    replies = []
    _make_bot(replies)._handle_update(_update("/estado", chat_id=999))
    assert replies == []


def test_mensaje_sin_comando_se_ignora():
    replies = []
    _make_bot(replies)._handle_update(_update("hola, todo bien?"))
    assert replies == []


def test_handler_que_falla_no_propaga_y_avisa():
    replies = []

    def boom():
        raise RuntimeError("se rompió")

    _make_bot(replies, handlers={"estado": boom})._handle_update(
        _update("/estado"))
    assert len(replies) == 1
    assert "no pude" in replies[0][1].lower()


def test_offset_avanza_para_no_repetir_updates():
    # Tras procesar un update, el offset debe quedar en update_id + 1.
    replies = []
    bot = _make_bot(replies)
    upd = _update("/estado", update_id=42)
    bot._offset = max(bot._offset, upd["update_id"] + 1)
    assert bot._offset == 43

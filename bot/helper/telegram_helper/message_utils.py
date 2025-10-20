from asyncio import sleep
from re import match as re_match
from time import time
from pytdbot.types import InputMessageReplyToMessage, MessageSendOptions

from ... import LOGGER, status_dict, task_dict_lock, intervals
from ...core.config_manager import Config
from ...core.telegram_client import TgClient
from ..ext_utils.bot_utils import SetInterval
from ..ext_utils.exceptions import TgLinkException
from ..ext_utils.status_utils import get_readable_message


async def send_message(message, text, buttons=None, block=True):
    res = await message.reply_text(
        text=text,
        disable_web_page_preview=True,
        disable_notification=True,
        reply_markup=buttons,
    )
    if res.is_error:
        if wait_for := res.limited_seconds:
            LOGGER.warning(res["message"])
            if block:
                await sleep(wait_for * 1.2)
                return await send_message(message, text, buttons)
        LOGGER.error(res["message"])
        return res["message"]
    return res


async def edit_message(message, text, buttons=None, block=True):
    res = await message.edit_text(
        text=text,
        disable_web_page_preview=True,
        reply_markup=buttons,
    )
    if res.is_error:
        if wait_for := res.limited_seconds:
            LOGGER.warning(res["message"])
            if block:
                await sleep(wait_for * 1.2)
                return await edit_message(message, text, buttons)
        LOGGER.error(res["message"])
        return res["message"]
    return res


async def send_file(message, file, caption=""):
    res = await message.reply_document(
        document=file, caption=caption, disable_notification=True
    )
    if res.is_error:
        if wait_for := res.limited_seconds:
            LOGGER.warning(res["message"])
            await sleep(wait_for * 1.2)
            return await send_file(message, file, caption)
        LOGGER.error(res["message"])
        return res["message"]
    return res


async def send_message_with_content(message, content):
    res = await message._client.sendMessage(
        chat_id=message.chat_id,
        message_thread_id=message.message_thread_id,
        reply_to=InputMessageReplyToMessage(message_id=message.id),
        options=MessageSendOptions(disable_notification=True),
        input_message_content=content,
    )
    if res.is_error:
        if wait_for := res.limited_seconds:
            LOGGER.warning(res["message"])
            await sleep(wait_for * 1.2)
            return await send_message_with_content(message, content)
    return res


async def send_album(message, contents):
    res = await TgClient.bot.sendMessageAlbum(
        chat_id=message.chat_id,
        message_thread_id=message.message_thread_id,
        reply_to=InputMessageReplyToMessage(message_id=message.id),
        options=MessageSendOptions(disable_notification=True),
        input_message_contents=contents,
    )
    if res.is_error:
        if wait_for := res.limited_seconds:
            LOGGER.warning(res["message"])
            await sleep(wait_for * 1.2)
            return await send_album(message, contents)
        LOGGER.error(res["message"])
        return [message]
    return res


async def send_rss(text, chat_id, thread_id):
    app = TgClient.user or TgClient.bot
    res = await app.sendTextMessage(
        chat_id=chat_id,
        text=text,
        disable_web_page_preview=True,
        message_thread_id=thread_id,
        disable_notification=True,
    )
    if res.is_error:
        if wait_for := res.limited_seconds:
            LOGGER.warning(res["message"])
            await sleep(wait_for * 1.2)
            return await send_rss(text)
        LOGGER.error(res["message"])
        return res["message"]
    return res


async def delete_message(message):
    res = await message.delete()
    if res.is_error:
        LOGGER.error(res["message"])


async def auto_delete_message(cmd_message=None, bot_message=None):
    await sleep(60)
    if cmd_message is not None:
        await delete_message(cmd_message)
    if bot_message is not None:
        await delete_message(bot_message)


async def delete_status():
    async with task_dict_lock:
        for key, data in list(status_dict.items()):
            try:
                await delete_message(data["message"])
                del status_dict[key]
            except Exception as e:
                LOGGER.error(str(e))


async def get_tg_link_message(link):
    message = None
    links = []
    if link.startswith("https://t.me/"):
        private = False
        msg = re_match(
            r"https:\/\/t\.me\/(?:c\/)?([^\/]+)(?:\/[^\/]+)?\/([0-9-]+)", link
        )
    else:
        private = True
        msg = re_match(
            r"tg:\/\/openmessage\?user_id=([0-9]+)&message_id=([0-9-]+)", link
        )
        if not TgClient.user:
            raise TgLinkException("USER_SESSION required for this private link!")

    chat = msg[1]
    msg_id = msg[2]
    if "-" in msg_id:
        start_id, end_id = msg_id.split("-")
        msg_id = start_id = int(start_id)
        end_id = int(end_id)
        btw = end_id - start_id
        if private:
            link = link.split("&message_id=")[0]
            links.append(f"{link}&message_id={start_id}")
            for _ in range(btw):
                start_id += 1
                links.append(f"{link}&message_id={start_id}")
        else:
            link = link.rsplit("/", 1)[0]
            links.append(f"{link}/{start_id}")
            for _ in range(btw):
                start_id += 1
                links.append(f"{link}/{start_id}")
    else:
        msg_id = int(msg_id)

    if chat.isdigit():
        chat = int(chat) if private else int(f"-100{chat}")

    if not private:
        message = await TgClient.bot.getMessage(chat_id=chat, message_id=msg_id)
        if message.is_error:
            private = True
            if not TgClient.user:
                raise TgLinkException(message["message"])

    if not private:
        return (links, "bot") if links else (message, "bot")
    elif TgClient.user:
        user_message = await TgClient.user.getMessage(chat_id=chat, message_id=msg_id)
        if user_message.is_error:
            raise TgLinkException(
                f"You don't have access to this chat!. ERROR: {user_message["message"]}"
            )
        return (links, "user") if links else (user_message, "user")
    else:
        raise TgLinkException("Private: Please report!")


async def temp_download(msg):
    res = await msg.download(synchronous=True)
    return res.path


async def update_status_message(sid, force=False):
    if intervals["stopAll"]:
        return
    async with task_dict_lock:
        if not status_dict.get(sid):
            if obj := intervals["status"].get(sid):
                obj.cancel()
                del intervals["status"][sid]
            return
        if not force and time() - status_dict[sid]["time"] < 3:
            return
        status_dict[sid]["time"] = time()
        page_no = status_dict[sid]["page_no"]
        status = status_dict[sid]["status"]
        is_user = status_dict[sid]["is_user"]
        page_step = status_dict[sid]["page_step"]
        text, buttons = await get_readable_message(
            sid, is_user, page_no, status, page_step
        )
        if text is None:
            del status_dict[sid]
            if obj := intervals["status"].get(sid):
                obj.cancel()
                del intervals["status"][sid]
            return
        if text != status_dict[sid]["message"].text:
            message = await edit_message(
                status_dict[sid]["message"], text, buttons, block=False
            )
            if isinstance(message, str):
                if message.startswith("Telegram says: [40"):
                    del status_dict[sid]
                    if obj := intervals["status"].get(sid):
                        obj.cancel()
                        del intervals["status"][sid]
                else:
                    LOGGER.error(
                        f"Status with id: {sid} haven't been updated. Error: {message}"
                    )
                return
            status_dict[sid]["message"].text = text
            status_dict[sid]["time"] = time()


async def send_status_message(msg, user_id=0):
    if intervals["stopAll"]:
        return
    sid = user_id or msg.chat_id
    is_user = bool(user_id)
    async with task_dict_lock:
        if sid in status_dict:
            page_no = status_dict[sid]["page_no"]
            status = status_dict[sid]["status"]
            page_step = status_dict[sid]["page_step"]
            text, buttons = await get_readable_message(
                sid, is_user, page_no, status, page_step
            )
            if text is None:
                del status_dict[sid]
                if obj := intervals["status"].get(sid):
                    obj.cancel()
                    del intervals["status"][sid]
                return
            old_message = status_dict[sid]["message"]
            message = await send_message(msg, text, buttons, block=False)
            if isinstance(message, str):
                LOGGER.error(
                    f"Status with id: {sid} haven't been sent. Error: {message}"
                )
                return
            await delete_message(old_message)
            message.text = text
            status_dict[sid].update({"message": message, "time": time()})
        else:
            text, buttons = await get_readable_message(sid, is_user)
            if text is None:
                return
            message = await send_message(msg, text, buttons, block=False)
            if isinstance(message, str):
                LOGGER.error(
                    f"Status with id: {sid} haven't been sent. Error: {message}"
                )
                return
            message.text = text
            status_dict[sid] = {
                "message": message,
                "time": time(),
                "page_no": 1,
                "page_step": 1,
                "status": "All",
                "is_user": is_user,
            }
        if not intervals["status"].get(sid) and not is_user:
            intervals["status"][sid] = SetInterval(
                Config.STATUS_UPDATE_INTERVAL, update_status_message, sid
            )

from loguru import logger
from sqlalchemy import select

from creart import create
from graia.saya import Channel
from graia.ariadne.app import Ariadne
from graia.broadcast import Broadcast
from graia.ariadne.message.element import Source
from graia.ariadne.message.chain import MessageChain
from graia.broadcast.interrupt import InterruptControl
from graia.ariadne.event.lifecycle import ApplicationLaunch
from graia.saya.builtins.broadcast.schema import ListenerSchema
from graia.ariadne.event.message import Group, Member, GroupMessage
from graia.ariadne.message.parser.twilight import Twilight, FullMatch, RegexMatch, RegexResult, WildcardMatch

from .utils import valid2send, get_image
from shared.orm import orm, TriggerKeyword
from shared.utils.waiter import ConfirmWaiter
from shared.models.config import GlobalConfig
from shared.utils.message_chain import parse_message_chain_as_stable_string
from shared.utils.control import Function, BlackListControl, UserCalledCountControl, Distribute


channel = Channel.current()
channel.name("Gallery")
channel.author("SAGIRI-kawaii")
channel.description(
    "一个可以发送图片的插件，在群中发送设置好的关键词即可\n"
    "发送 `添加图库关键词#{图库名（配置文件中路径key值）}#{keyword}即可进行关键词的添加\n"
    "发送 `删除图库关键词#{图库名（配置文件中路径key值）}即可进行关键词的删除\n"
    "发送 `查看图库关键词#{图库名（配置文件中路径key值）}即可进行关键词的查看`"
)

config = create(GlobalConfig)


@channel.use(
    ListenerSchema(
        listening_events=[ApplicationLaunch]
    )
)
async def db_init():
    for key in config.gallery.keys():
        try:
            await orm.insert_or_ignore(
                TriggerKeyword,
                [TriggerKeyword.keyword == key, TriggerKeyword.function == key],
                {"keyword": key, "function": key},
            )
        except Exception as e:
            logger.error(e)


@channel.use(
    ListenerSchema(
        listening_events=[GroupMessage],
        inline_dispatchers=[
            Twilight([
                FullMatch("添加图库关键词"),
                FullMatch("#"),
                RegexMatch(r"[\w\W]+") @ "gallery_name",
                FullMatch("#"),
                WildcardMatch() @ "keyword"
            ])
        ],
        decorators=[
            Distribute.distribute(),
            Function.require(channel.module),
            BlackListControl.enable(),
            UserCalledCountControl.add(UserCalledCountControl.FUNCTIONS),
        ],
    )
)
async def add_keyword(app: Ariadne, group: Group, gallery_name: RegexResult, keyword: RegexResult):
    gallery_name = gallery_name.result.display.strip()
    keyword = keyword.result.as_persistent_string()
    if gallery_name not in config.gallery.keys():
        return await app.send_group_message(
            group, MessageChain(f"不存在这个图库哦，目前有以下图库：{'、'.join(config.gallery.keys())}")
        )
    if await orm.fetchone(select(TriggerKeyword.keyword).where(TriggerKeyword.keyword == keyword)):
        return await app.send_group_message(group, MessageChain("已存在的关键词！请先删除！"))
    await orm.insert_or_ignore(
        TriggerKeyword,
        [TriggerKeyword.keyword == keyword, TriggerKeyword.function == gallery_name],
        {"keyword": keyword, "function": gallery_name},
    )
    return MessageChain(f"关键词添加成功！\n{keyword} -> {gallery_name}")


@channel.use(
    ListenerSchema(
        listening_events=[GroupMessage],
        inline_dispatchers=[
            Twilight([
                FullMatch("删除图库关键词"),
                FullMatch("#"),
                WildcardMatch() @ "keyword"
            ])
        ],
        decorators=[
            Distribute.distribute(),
            Function.require(channel.module),
            BlackListControl.enable(),
            UserCalledCountControl.add(UserCalledCountControl.FUNCTIONS),
        ],
    )
)
async def delete_keyword(app: Ariadne, group: Group, member: Member, keyword: RegexResult):
    if record := await orm.fetchone(select(TriggerKeyword.function).where(TriggerKeyword.keyword == keyword)):
        await app.send_group_message(group, MessageChain(f"查找到以下信息：\n{keyword} -> {record[0]}\n是否删除？（是/否）"))
        if await InterruptControl(create(Broadcast)).wait(ConfirmWaiter(group, member)):
            _ = await orm.delete(TriggerKeyword, [TriggerKeyword.keyword == keyword])
            return app.send_group_message(group, MessageChain(f"关键词 {keyword} 删除成功"))
        await app.send_group_message(group, MessageChain("非预期/确认回复，进程退出"))


@channel.use(
    ListenerSchema(
        listening_events=[GroupMessage],
        inline_dispatchers=[
            Twilight([
                FullMatch("查看图库关键词"),
                FullMatch("#"),
                WildcardMatch() @ "gallery_name"
            ])
        ],
        decorators=[
            Distribute.distribute(),
            Function.require(channel.module),
            BlackListControl.enable(),
            UserCalledCountControl.add(UserCalledCountControl.FUNCTIONS),
        ],
    )
)
async def show_keywords(app: Ariadne, group: Group, gallery_name: RegexResult):
    gallery_name = gallery_name.result.display.strip()
    if keywords := await orm.fetchall(
        select(TriggerKeyword.keyword).where(TriggerKeyword.function == gallery_name)
    ):
        return await app.send_group_message(group, MessageChain("\n".join([keyword[0] for keyword in keywords])))
    await app.send_group_message(group, MessageChain(f"未找到图库{gallery_name}对应关键词或图库名错误！"))


@channel.use(
    ListenerSchema(
        listening_events=[GroupMessage],
        inline_dispatchers=[Twilight([FullMatch("查看图库列表")])],
        decorators=[
            Distribute.distribute(),
            Function.require(channel.module),
            BlackListControl.enable(),
            UserCalledCountControl.add(UserCalledCountControl.FUNCTIONS),
        ],
    )
)
async def show_keywords(app: Ariadne, group: Group):
    await app.send_group_message(group, MessageChain(f"目前有以下图库：{'、'.join(config.gallery.keys())}"))


@channel.use(
    ListenerSchema(
        listening_events=[GroupMessage],
        decorators=[
            Distribute.distribute(),
            Function.require(channel.module),
            BlackListControl.enable(),
            UserCalledCountControl.add(UserCalledCountControl.FUNCTIONS),
        ],
    )
)
async def keyword_detect(app: Ariadne, group: Group, member: Member, message: MessageChain, source: Source):
    message = parse_message_chain_as_stable_string(message)
    if resp := await orm.fetchone(select(TriggerKeyword.function).where(TriggerKeyword.keyword == message)):
        gallery_name = resp[0]
        valid = await valid2send(group, member, gallery_name)
        if valid == "PermissionError":
            return await app.send_group_message(group, MessageChain("你的权限不足捏"), quote=source)
        elif valid == "IntervalError":
            return await app.send_group_message(group, MessageChain("这群怎么这么活跃啊，歇会儿吧（"), quote=source)
        await app.send_group_message(group, MessageChain(await get_image(gallery_name)))

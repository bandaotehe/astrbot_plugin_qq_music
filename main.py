from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
from astrbot.api import logger
import requests


@register("astrbot_plugin_qq_music", "bandaotehe", "点歌插件", "1.0", "https://github.com/bandaotehe/astrbot_plugin_qq_music")
class MusicPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config  #配置文件
        self.user_data = {}  # 存储用户搜索结果的缓存
        self.music_server = self.config.get("music_server", "") if self.config.get("music_server", "") else "http://120.48.77.142:3200/"

    def _get_cache_key(self, event: AstrMessageEvent) -> str:
        """生成用户缓存键（区分私聊/群聊）"""
        return f"{event.get_group_id()}_{event.get_sender_id()}" if event.get_group_id() else str(event.get_sender_id())

    @filter.command("点歌")
    async def search_music(self, event: AstrMessageEvent):
        """搜索歌曲：/点歌 [关键词]"""
        keyword = event.get_message_str()
        logger.info(f"用户 {event.get_sender_id()} 搜索歌曲：{keyword}")
        if not keyword:
            yield event.plain_result("请输入搜索关键词，例如：/点歌 七里香")
            return

        try:
            # 提取歌名
            keyword = keyword.replace("点歌", "", 1).strip()
            # 调用搜索接口
            logger.info(f"开始搜索歌曲：{keyword}")
            results = self.search(keyword)
            if not results:
                yield event.plain_result("没有找到相关歌曲")
                return

            # 存储结果到缓存
            cache_key = self._get_cache_key(event)
            self.user_data[cache_key] = results

            # 构建回复消息
            response = ["找到以下歌曲："]
            for i, song in enumerate(results, 1):
                response.append(f"{i}. {song['title']} - {song['artist']}")
            response.append("\n发送 /播放 + 序号 播放歌曲（例如：/播放 1）")

            yield event.plain_result("\n".join(response))

        except Exception as e:
            logger.error(f"搜索失败: {str(e)}")
            yield event.plain_result("歌曲搜索失败，请稍后再试")

    @filter.command("播放")
    async def play_music(self, event: AstrMessageEvent):
        """播放歌曲：/播放 [序号]"""
        cache_key = self._get_cache_key(event)
        if cache_key not in self.user_data:
            yield event.plain_result("请先使用 /点歌 搜索歌曲")
            return

        try:
            # 获取用户输入的序号
            index_str = event.message_str.strip()
            if not index_str.isdigit():
                yield event.plain_result("请输入有效的歌曲序号")
                return

            index = int(index_str) - 1
            songs = self.user_data[cache_key]

            # 验证序号有效性
            if index < 0 or index >= len(songs):
                yield event.plain_result(f"序号无效，请输入1~{len(songs)}之间的数字")
                return

            # 获取歌曲信息
            song_id = songs[index]["id"]
            song_info = self.getSongInfo(song_id)
            song_url = self.getSongUrl(song_id)

            # 构建回复消息
            response = [
                f"🎵 正在播放: {song_info['title']}",
                f"👤 歌手: {song_info['artist']}",
                f"💽 专辑: {song_info['album']}",
                f"⏱ 时长: {song_info['duration']}",
                f"🔗 播放链接: {song_url}"
            ]

            # 发送带封面的卡片消息（实际根据平台支持调整）
            yield event.card_result(
                title=song_info["title"],
                content="\n".join(response),
                image=song_info["cover"],
                buttons=[{"text": "在线播放", "link": song_url}]
            )

        except Exception as e:
            logger.error(f"播放失败: {str(e)}")
            yield event.plain_result("歌曲播放失败，请稍后再试")

    async def terminate(self):
        """清理资源"""
        self.user_data.clear()
        logger.info("点歌插件已卸载")

    def search(self, keyword: str):
        """搜索歌曲"""
        response = requests.get(f"{self.music_server}getSmartbox?key={keyword}")
        data = response.json()
        song_list = data["response"]["data"]["song"]["itemlist"]
        return song_list


    def getSongUrl(self, song_id: int):
        """模拟获取歌曲链接"""
        return f"https://music.example.com/play/{song_id}"

    def getSongInfo(self, song_id: int):
        """模拟获取歌曲信息"""
        return {
            "title": "七里香" if song_id == 1 else "稻香" if song_id == 2 else "晴天",
            "artist": "周杰伦",
            "album": "七里香" if song_id == 1 else "魔杰座" if song_id == 2 else "叶惠美",
            "duration": "04:30",
            "cover": f"https://cover.example.com/{song_id}.jpg"
        }

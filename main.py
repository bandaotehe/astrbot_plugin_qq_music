import os
from urllib.parse import urlparse

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.api import logger
import requests
import soundfile as sf


@register("astrbot_plugin_qq_music", "bandaotehe", "点歌插件", "1.0", "https://github.com/bandaotehe/astrbot_plugin_qq_music")
class MusicPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config  #配置文件
        self.user_data = {}  # 存储用户搜索结果的缓存
        self.music_server = self.config.get("music_server", "") if self.config.get("music_server", "") else "https://api.vkeys.cn/"

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
                response.append(f"{i}. {song['song']} - {song['singer']}")
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
            index_str = event.get_message_str().strip()
            index_str = index_str.replace("播放", "", 1).strip()
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
            song_id = songs[index]["mid"]
            song_url = self.getSongUrl(song_id,event)
            ##转换音频格式
            output = self.flac_to_wav_from_url(song_url)
            chain = [
                Comp.Record(file=output, url=output)
            ]
            yield event.chain_result(chain)
            return

        except Exception as e:
            logger.error(f"播放失败: {str(e)}")
            yield event.plain_result("歌曲播放失败，请稍后再试")

    async def terminate(self):
        """清理资源"""
        self.user_data.clear()
        logger.info("点歌插件已卸载")

    def search(self, keyword: str):
        """搜索歌曲"""
        response = requests.get(f"{self.music_server}v2/music/tencent/search/song?word={keyword}")
        data = response.json()
        logger.info(f"搜索结果：{data}")
        data = data["data"]
        songs = []
        for song in data:
            song_dict = {
                "mid": song["mid"],
                "song": song["song"],
                "subtitle": song["subtitle"],
                "singer": song["singer"],
                "interval": song["interval"],
                "album": song["album"],
            }
            songs.append(song_dict)
        return songs

    def getSongUrl(self, song_id: str, event: AstrMessageEvent):
        try:
            response = requests.get(f"{self.music_server}v2/music/tencent/geturl?mid={song_id}")
            response.raise_for_status()  # 检查 HTTP 错误
            data = response.json()
        except Exception as e:
            logger.error(f"❌ 请求歌曲链接失败: {e}")
            event.plain_result("❌ 获取歌曲信息失败，请稍后重试。")
            return None
        # 安全查找 playUrl
        if data["code"] != 200:
            logger.error(f"❌ 请求歌曲链接失败")
            event.plain_result("未找到该歌曲链接")
        logger.info(f"播放链接：{data}")
        play_url = data.get("data", {}).get("url", {})
        if play_url:
            logger.info(f"播放链接：{play_url}")
            return play_url
        else:
            logger.error("❌ 无法播放，原因：链接为空或报错")
            event.plain_result("❌ 无法播放，原因：链接为空或报错")
            return None  # 明确返回 None 表示失败

    def download_flac(self,url, local_path=None):
        """
        从网络URL下载FLAC文件到本地
        :param url: FLAC文件的网络URL
        :param local_path: 本地保存路径(可选)
        :return: 本地文件路径
        """
        if local_path is None:
            # 从URL中提取文件名
            parsed = urlparse(url)
            filename = os.path.basename(parsed.path.split('?')[0])  # 去除查询参数
            local_path = filename
        # 分块下载大文件
        response = requests.get(url, stream=True)
        response.raise_for_status()  # 检查请求是否成功
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:  # 过滤掉保持连接的空白块
                    f.write(chunk)
        return local_path

    def flac_to_wav_from_url(self, flac_url, output_wav=None, keep_flac=False):
        """
        从网络URL下载FLAC并转换为WAV
        :param flac_url: FLAC文件的网络URL
        :param output_wav: 输出WAV文件路径(可选)
        :param keep_flac: 是否保留下载的FLAC文件
        :return: WAV文件路径
        """
        # 下载FLAC文件
        try:
            local_flac = self.download_flac(flac_url)
        except Exception as e:
            return None
        # 设置输出WAV路径
        if output_wav is None:
            output_wav = local_flac.replace('.flac', '.wav')
        # 转换FLAC到WAV
        try:
            data, samplerate = sf.read(local_flac)
            sf.write(output_wav, data, samplerate)
        except Exception as e:
            logger.error(f"FLAC转换WAV失败: {e}")
            return None
        finally:
            # 清理临时FLAC文件
            if not keep_flac and os.path.exists(local_flac):
                os.remove(local_flac)
        return output_wav


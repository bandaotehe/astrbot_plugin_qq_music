import os
from urllib.parse import urlparse
from typing import Dict, List, Optional, Generator, Any

import numpy as np
import requests
import soundfile as sf
import librosa

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.api import logger


@register(
    "astrbot_plugin_qq_music",
    "bandaotehe",
    "点歌插件",
    "1.0",
    "https://github.com/bandaotehe/astrbot_plugin_qq_music"
)
class MusicPlugin(Star):
    """QQ音乐点歌插件，支持搜索和播放音乐"""

    DEFAULT_MUSIC_SERVER = "https://api.vkeys.cn/"
    MAX_AUDIO_SIZE_MB = 5  # 最大音频文件大小限制(MB)
    DEFAULT_SAMPLE_RATE = 22050  # 默认采样率

    def __init__(self, context: Context, config: AstrBotConfig):
        """初始化插件"""
        super().__init__(context)
        self.config = config
        self.user_data: Dict[str, List[Dict[str, Any]]] = {}  # 存储用户搜索结果的缓存
        self.music_server = (
                self.config.get("music_server", "")
                or self.DEFAULT_MUSIC_SERVER
        )

    def _get_cache_key(self, event: AstrMessageEvent) -> str:
        """
        生成用户缓存键（区分私聊/群聊）
        Args:
            event: 消息事件对象
        Returns:
            缓存键字符串
        """
        if event.get_group_id():
            return f"{event.get_group_id()}_{event.get_sender_id()}"
        return str(event.get_sender_id())

    @filter.command("点歌")
    async def search_music(self, event: AstrMessageEvent) -> Generator[MessageEventResult, None, None]:
        """
        搜索歌曲
        Args:
            event: 消息事件对象
        Yields:
            消息响应结果
        """
        keyword = event.get_message_str().replace("点歌", "", 1).strip()
        logger.info(f"用户 {event.get_sender_id()} 搜索歌曲：{keyword}")

        if not keyword:
            yield event.plain_result("请输入搜索关键词，例如：/点歌 七里香")
            return

        try:
            logger.info(f"开始搜索歌曲：{keyword}")
            results = self._search_songs(keyword)

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
    async def play_music(self, event: AstrMessageEvent) -> Generator[MessageEventResult, None, None]:
        """
        播放歌曲

        Args:
            event: 消息事件对象

        Yields:
            消息响应结果
        """
        cache_key = self._get_cache_key(event)
        if cache_key not in self.user_data:
            yield event.plain_result("请先使用 /点歌 搜索歌曲")
            return

        try:
            index_str = event.get_message_str().replace("播放", "", 1).strip()

            if not index_str.isdigit():
                yield event.plain_result("请输入有效的歌曲序号")
                return

            index = int(index_str) - 1
            songs = self.user_data[cache_key]

            if index < 0 or index >= len(songs):
                yield event.plain_result(f"序号无效，请输入1~{len(songs)}之间的数字")
                return

            song_id = songs[index]["mid"]
            song_url = self._get_song_url(song_id)

            if not song_url:
                yield event.plain_result("获取歌曲链接失败")
                return

            output_path = self._convert_audio_format(song_url)

            if not output_path:
                yield event.plain_result("音频转换失败")
                return

            chain = [Comp.Record(file=output_path, url=output_path)]
            yield event.chain_result(chain)

        except Exception as e:
            logger.error(f"播放失败: {str(e)}")
            yield event.plain_result("歌曲播放失败，请稍后再试")

    async def terminate(self):
        """清理资源"""
        self.user_data.clear()
        logger.info("点歌插件已卸载")

    def _search_songs(self, keyword: str) -> List[Dict[str, Any]]:
        """
        搜索歌曲

        Args:
            keyword: 搜索关键词

        Returns:
            歌曲信息列表
        """
        try:
            url = f"{self.music_server}v2/music/tencent/search/song?word={keyword}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            data = response.json().get("data", [])
            logger.info(f"搜索结果：{data}")

            return [
                {
                    "mid": song["mid"],
                    "song": song["song"],
                    "subtitle": song["subtitle"],
                    "singer": song["singer"],
                    "interval": song["interval"],
                    "album": song["album"],
                }
                for song in data
            ]

        except Exception as e:
            logger.error(f"搜索歌曲失败: {str(e)}")
            return []

    def _get_song_url(self, song_id: str) -> Optional[str]:
        """
        获取歌曲播放链接

        Args:
            song_id: 歌曲ID

        Returns:
            歌曲播放URL或None
        """
        try:
            url = f"{self.music_server}v2/music/tencent/geturl?mid={song_id}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("code") != 200:
                logger.error(f"获取歌曲链接失败: {data}")
                return None

            play_url = data.get("data", {}).get("url")
            logger.info(f"播放链接：{play_url}")
            return play_url

        except Exception as e:
            logger.error(f"获取歌曲链接失败: {str(e)}")
            return None

    def _download_audio_file(self, url: str, local_path: Optional[str] = None) -> Optional[str]:
        """
        下载音频文件

        Args:
            url: 音频文件URL
            local_path: 本地保存路径(可选)

        Returns:
            本地文件路径或None
        """
        try:
            if local_path is None:
                parsed = urlparse(url)
                filename = os.path.basename(parsed.path.split('?')[0])
                local_path = filename

            logger.info(f"开始下载音频文件: {url}")

            with requests.get(url, stream=True, timeout=30) as response:
                response.raise_for_status()

                with open(local_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

            file_size = os.path.getsize(local_path) / (1024 * 1024)
            logger.info(f"音频文件下载完成: {local_path} (大小: {file_size:.2f}MB)")
            return local_path

        except Exception as e:
            logger.error(f"下载音频文件失败: {url} - 错误: {str(e)}")
            return None

    def _convert_audio_format(
            self,
            audio_url: str,
            target_size_mb: int = MAX_AUDIO_SIZE_MB,
            output_path: Optional[str] = None,
            keep_original: bool = False
    ) -> Optional[str]:
        """
        转换音频格式并控制文件大小

        Args:
            audio_url: 音频文件URL
            target_size_mb: 目标文件大小(MB)
            output_path: 输出路径(可选)
            keep_original: 是否保留原始文件

        Returns:
            转换后的文件路径或None
        """
        try:
            # 下载音频文件
            local_path = self._download_audio_file(audio_url)
            if not local_path:
                return None

            # 读取音频数据
            data, sample_rate = sf.read(local_path)
            duration = len(data) / sample_rate
            channels = data.shape[1] if len(data.shape) > 1 else 1

            # 计算当前参数的文件大小
            original_size_mb = (sample_rate * 16 * channels * duration) / (8 * 1024 * 1024)
            logger.info(f"原始 WAV 预估大小: {original_size_mb:.2f}MB")

            # 自动调整参数以接近目标大小
            adjusted_sample_rate = sample_rate
            adjusted_channels = channels

            if original_size_mb > target_size_mb:
                if sample_rate > self.DEFAULT_SAMPLE_RATE:
                    adjusted_sample_rate = self.DEFAULT_SAMPLE_RATE
                elif channels == 2:
                    adjusted_channels = 1

                # 应用调整
                if adjusted_sample_rate != sample_rate:
                    data = librosa.resample(
                        data.T,
                        orig_sr=sample_rate,
                        target_sr=adjusted_sample_rate
                    ).T

                if adjusted_channels == 1 and channels == 2:
                    data = np.mean(data, axis=1, keepdims=True)

            # 设置输出路径
            if output_path is None:
                output_path = local_path.replace(".flac", ".wav")

            # 写入 WAV 文件
            sf.write(
                output_path,
                data,
                adjusted_sample_rate,
                subtype="PCM_16",
            )

            # 验证最终文件大小
            final_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"转换完成: {output_path} (大小: {final_size_mb:.2f}MB)")

            # 清理临时文件
            if not keep_original and os.path.exists(local_path):
                os.remove(local_path)

            return output_path

        except Exception as e:
            logger.error(f"音频转换失败: {str(e)}")
            return None

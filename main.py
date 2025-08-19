import os
from urllib.parse import urlparse

import numpy as np

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.api import logger
import requests
import soundfile as sf
import librosa


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
            logger.info(f"开始获取歌曲信息：{song_id}")
            song_url = self.gets_ong_url(song_id)
            logger.info(f"歌曲链接：{song_url}")
            ##转换音频格式
            output = self.flac_to_wav_with_size_control(song_url)
            logger.info(f"转换后的文件路径：{output}")
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

    def gets_ong_url(self, song_id: str):
        logger.info(f"开始获取歌曲信息gets_ong_url：{song_id}")
        try:
            response = requests.get(f"{self.music_server}v2/music/tencent/geturl?mid={song_id}")
            response.raise_for_status()  # 检查 HTTP 错误
            data = response.json()
        except Exception as e:
            logger.error(f"❌ 请求歌曲链接失败: {e}")
            raise
        logger.info(f"播放链接：{data}")
        # 安全查找 playUrl
        if data["code"] != 200:
            logger.error(f"❌ 请求歌曲链接失败")
        play_url = data.get("data", {}).get("url", {})
        if play_url:
            logger.info(f"播放链接：{play_url}")
            return play_url
        else:
            logger.error("❌ 无法播放，原因：链接为空或报错")
            raise

    def download_flac(self, url, local_path=None):
        """
        从网络URL下载FLAC文件到本地
        :param url: FLAC文件的网络URL
        :param local_path: 本地保存路径(可选)
        :return: 本地文件路径
        """
        try:
            if local_path is None:
                # 从URL中提取文件名
                parsed = urlparse(url)
                filename = os.path.basename(parsed.path.split('?')[0])  # 去除查询参数
                local_path = filename
                logger.debug(f"自动生成本地保存路径: {local_path}")
            logger.info(f"开始下载FLAC文件: {url}")
            # 分块下载大文件
            response = requests.get(url, stream=True)
            response.raise_for_status()  # 检查请求是否成功
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:  # 过滤掉保持连接的空白块
                        f.write(chunk)
            file_size = os.path.getsize(local_path) / (1024 * 1024)  # 计算文件大小(MB)
            logger.info(f"FLAC文件下载完成: {local_path} (大小: {file_size:.2f}MB)")
            return local_path
        except Exception as e:
            logger.error(f"下载FLAC文件失败: {url} - 错误: {str(e)}")
            raise  # 重新抛出异常以便上层处理

    def flac_to_wav_with_size_control(
            self,
            flac_url,
            target_size_mb=5,
            output_wav=None,
            keep_flac=False,
    ) -> str :
        """
        从 URL 下载 FLAC 并转换为 WAV，文件大小控制在目标值附近
        :param flac_url: FLAC 文件 URL
        :param target_size_mb: 目标文件大小（MB，默认 5MB）
        :param output_wav: 输出路径（可选）
        :param keep_flac: 是否保留 FLAC 文件
        :return: WAV 文件路径
        """
        try:
            # 下载 FLAC 文件
            local_flac = self.download_flac(flac_url)
            logger.info(f"FLAC 下载完成: {local_flac}")

            # 读取音频数据
            data, samplerate = sf.read(local_flac)
            duration = len(data) / samplerate  # 音频时长（秒）
            channels = data.shape[1] if len(data.shape) > 1 else 1  # 声道数
            # 计算当前参数的文件大小
            original_size_mb = (samplerate * 16 * channels * duration) / (8 * 1024 * 1024)
            logger.info(f"原始 WAV 预估大小: {original_size_mb:.2f}MB")
            # 自动调整参数以接近目标大小
            if original_size_mb > target_size_mb:
                # 优先降低采样率，再降低比特深度，最后转单声道
                adjusted_samplerate = samplerate
                adjusted_subtype = "PCM_16"
                adjusted_channels = channels
                # 逐步调整参数
                while original_size_mb > target_size_mb * 1.1:  # 留 10% 余量
                    if adjusted_samplerate > 22050:
                        adjusted_samplerate = 22050  # 降到 22.05kHz
                    elif adjusted_channels == 2:
                        adjusted_channels = 1  # 转单声道
                    else:
                        break  # 无法再压缩

                    # 重新计算大小
                    original_size_mb = (adjusted_samplerate * (
                        8 if adjusted_subtype == "PCM_8" else 16) * adjusted_channels * duration) / (8 * 1024 * 1024)
                logger.info(f"调整后参数: {adjusted_samplerate}Hz, {adjusted_subtype}, {adjusted_channels}声道")
                # 应用调整
                if adjusted_samplerate != samplerate:
                    data = librosa.resample(data.T, orig_sr=samplerate, target_sr=adjusted_samplerate).T
                if adjusted_channels == 1 and channels == 2:
                    data = np.mean(data, axis=1, keepdims=True)  # 立体声 → 单声道

            # 设置输出路径
            if output_wav is None:
                output_wav = local_flac.replace(".flac", ".wav")

            # 写入 WAV 文件
            sf.write(
                output_wav,
                data,
                adjusted_samplerate if 'adjusted_samplerate' in locals() else samplerate,
                subtype=adjusted_subtype if 'adjusted_subtype' in locals() else "PCM_16",
            )
            # 验证最终文件大小
            final_size_mb = os.path.getsize(output_wav) / (1024 * 1024)
            logger.info(f"转换完成: {output_wav} (大小: {final_size_mb:.2f}MB)")
            return output_wav
        except Exception as e:
            logger.error(f"转换失败: {e}")
            raise
        finally:
            if not keep_flac and os.path.exists(local_flac):
                os.remove(local_flac)


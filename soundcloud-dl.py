#!/usr/bin/env python3
# UwU ~
import os
import re
import sys
import json
import utils
import base64
import config
import shutil
import aiohttp
import asyncio
import aiofiles
from argparse import ArgumentParser

from mutagen.oggopus import OggOpus
from mutagen.mp4 import MP4, MP4Cover
from mutagen.flac import FLAC, Picture
from mutagen.oggvorbis import OggVorbis
from mutagen.id3 import ID3, TIT2, TRCK, TALB, TPE1, TPE2, TDRC, COMM, APIC, TCON, TXXX, PictureType

_CONCURRENT_TRACKS = 2
_CONCURRENT_SEGMENTS = 8
_LOSSLESS_REGEX = r"alac|ape|flac|pcm_(f|s|u).+"
_EXT_MAP = {
    'aac'   : 'm4a',
    'opus'  : 'opus',
    'mp3'   : 'mp3',
    'vorbis': 'ogg',
    'flac'  : 'flac'
}

class FfmpegNotInPathError(Exception):
    pass

class SCSessionClosedError(Exception):
    pass

class SCIncorrectUrlException(Exception):
    pass

class SCInvalidToken(Exception):
    pass

class SoundCloudDL:
    _session: aiohttp.ClientSession = None
    _aenters: int = 0 # so that multiple 'async with' constructs wont replace session

    _client_id  : str = None

    directory        : str  = '.'
    oauth_token      : str  = None
    prefer_opus      : bool = False
    low_quality      : bool = False
    download_original: bool = True
    process_original : bool = True
    compression_level: int  = 12
    
    def __init__(self) -> None:
        for executable in ['ffmpeg', 'ffprobe']:
            if not shutil.which(executable):
                raise FfmpegNotInPathError('ffmpeg and/or ffprobe is not in PATH')
    
    async def __aenter__(self):
        if self._aenters < 1:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(force_close=True),
                timeout=aiohttp.ClientTimeout(total=None),
                headers={"Authorization": f"OAuth {self.oauth_token}"} if self.oauth_token else {}
            )
        self._aenters += 1
        if not self._client_id:
            await self._scrape_client_id()
        if self.oauth_token:
            async with self._session.get(
                'https://api-v2.soundcloud.com/payments/quotations/consumer-subscription',
                params={'client_id': self._client_id}
            ) as r:
                if r.status == 401:
                    raise SCInvalidToken()
                sub_json = await r.json()
                if sub_json['active_subscription']['package']['plan'] == 'consumer-high-tier':
                    print('go+ account (aac)')
                else:
                    print('free/go account (no aac)')
        else:
            print('no account (no aac)')
        print(f"client_id = {self._client_id}")
        return self
    
    async def __aexit__(self, *_) -> None:
        self._aenters -= 1
        if not self._aenters:
            await self._session.close()
        self._session = None

    async def _extract_client_id(self, url: str) -> str:
        async with self._session.get(url) as r:
            try:
                content = await r.text()
            except:
                return
        match = re.search(r'"client_id=(.+?)"', content)
        if match: return match.group(1)

    async def _scrape_client_id(self) -> None:
        async with self._session.get('https://soundcloud.com/discover') as r:
            content = await r.text()
        js_urls = re.findall(r'<script crossorigin src="(https://.*\.sndcdn\.com/assets/.+\.js)"></script>', content)

        tasks = [
            asyncio.create_task(
                self._extract_client_id(url)
            ) for url in js_urls
        ]

        for task in asyncio.as_completed(tasks):
            client_id = await task
            if client_id:
                self._client_id = client_id
                break

        for task in tasks:
            if not task.done(): task.cancel()

    async def _resolve_url(self, url: str) -> dict:
        async with self._session.get(
            "https://api-v2.soundcloud.com/resolve",
            params={
                "url": url,
                "client_id": self._client_id
            }
        ) as r:
            data = await r.json()
        return data
    
    async def _get_track(self, track_id: int, secret_token = None) -> dict:
        params = {"client_id": self._client_id} 
        if secret_token: params.extend({'secret_token': secret_token})
        async with self._session.get(
            f"https://api-v2.soundcloud.com/tracks/{track_id}",
            params=params
        ) as r:
            data = await r.json()
        return data
    
    async def _clean_url(self, url: str) -> str:
        if url.startswith('https://soundcloud.app.goo.gl/') or url.startswith('https://on.soundcloud.com/'):
            async with self._session.get(url, allow_redirects=False) as r:
                url = r.headers['Location']
        if url.startswith('https://m.'):
            url = url.replace('m.', '', 1)
        url = url.partition('?')[0].partition('#')[0].strip('/')
        return url
    
    def _get_link_type(self, url: str) -> str:
        for link_type,   pattern in [
            ("user",     r"https://soundcloud\.com/[^/]+(/tracks|/popular-tracks)?"),
            ("likes",    r"https://soundcloud\.com/[^/]+/likes"),
            ("reposts",  r"https://soundcloud\.com/[^/]+/reposts"),
            ("track",    r"https://soundcloud\.com/[^/]+/[^/]+(/s-[^/]+)?"),
            ("playlist", r"https://soundcloud\.com/[^/]+/sets/[^/]+(/s-[^/]+)?"),
        ]:
            if re.fullmatch(pattern, url):
                return link_type

    async def _download_file(self, url: str, dest: str) -> dict:
        async with self._session.get(url) as r:
            async with aiofiles.open(dest, 'wb') as f:
                await f.write(await r.read())
        return dict(r.headers)

    async def _get_cover_url(self, data: dict) -> str:
        url = data['artwork_url']
        if not url:
            return None
        uwu = url.rpartition('-')[0] + "-original"

        # could make a call to them all like to .js files in _scrape_client_id() but 99% of the time it's jpg, otherwise png
        # the rest is for safety ig
        for ext in ["jpg", "png", "jpeg" "pjp", "pjpeg", "jfif"]:
            url = f"{uwu}.{ext}"
            async with self._session.get(url) as r:
                if r.status == 200:
                    return url

    async def _tag(self, path: str, data: dict, album: str = None, album_artist: str = None, track: tuple[int, int] = None):
        tags = None
        ext = path.rpartition('.')[-1]

        date = (data['release_date'] if data['release_date'] else data['created_at']).partition('T')[0]
        cover_url = await self._get_cover_url(data)

        if cover_url:
            async with self._session.get(cover_url) as r:
                cover_data = await r.read()
            if cover_url.endswith('.png'):
                cover_mime = 'image/png'
            else:
                cover_mime = 'image/jpeg'

        match ext:  
            case "m4a":
                tags = MP4(path)
                tags["\xa9nam"] = data["title"]
                tags["\xa9alb"] = album if album else data['title']
                tags["\xa9ART"] = data["user"]["username"]
                tags["aART"] = album_artist if album_artist else data["user"]["username"]
                tags["\xa9day"] = date
                tags["----:com.apple.iTunes:url"] = bytes(data['permalink_url'], 'UTF-8')
                if data.get('genre'): tags['\xa9gen'] = data['genre']
                if track: tags['trkn'] = [track]
                if data.get('description'): tags['\xa9cmt'] = data['description']
                if cover_url:
                    tags['covr'] = [MP4Cover(cover_data, imageformat=(
                        MP4Cover.FORMAT_PNG if cover_mime == 'image/png' else MP4Cover.FORMAT_JPEG
                    ))]
            case "mp3":
                tags = ID3(path)
                tags.add(TIT2(text=data['title']))
                tags.add(TALB(text=(album if album else data['title'])))
                tags.add(TPE1(text=data["user"]["username"]))
                tags.add(TPE2(text=(album_artist if album_artist else data["user"]["username"])))
                tags.add(TDRC(text=date))
                tags.add(TXXX(desc="URL", text=data['permalink_url']))
                if data.get('genre'): tags.add(TCON(text=data['genre']))
                if track: tags.add(TRCK(text="/".join(map(str, track))))
                if data.get('description'): tags.add(COMM(text=data["description"]))
                if cover_url:
                    tags.add(APIC(mime=cover_mime, desc="Front Cover", data=cover_data))
            # these are *similiar* but not the same
            case "opus" | "flac" | "ogg":
                match ext:
                    case "opus":
                        tags = OggOpus(path)
                    case "flac":
                        tags = FLAC(path)
                    case "ogg":
                        tags = OggVorbis(path)
                tags["title"] = data['title']
                tags["album"] = album if album else data['title']
                tags["artist"] = data["user"]["username"]
                tags["albumartist"] = album_artist if album_artist else data["user"]["username"]
                tags["date"] = date
                tags["url"] = data['permalink_url']
                if data.get('genre'): tags['genre'] = data['genre']
                if track: tags["tracknumber"], tags['tracktotal'] = map(str, track)
                if data.get('description'): tags['comment'] = data['description']
                if cover_url:
                    picture = Picture()
                    picture.data = cover_data
                    picture.mime = cover_mime
                    picture.type = PictureType.COVER_FRONT
                    if ext == 'flac':
                        tags.add_picture(picture)
                    else:
                        picture_data = picture.write()
                        encoded_data = base64.b64encode(picture_data)
                        vcomment_value = encoded_data.decode("ascii")
                        tags["metadata_block_picture"] = [vcomment_value]
        if tags:
            tags.save(path)

    async def _download_track(self, data: dict, subdir: str = '.', album: str = None, album_artist: str = None, track: tuple[int, int] = None) -> bool:
        # create array of desired quality and progressively less desired fallbacks
        codecs = ['aac'] if not self.low_quality else []
        codecs.extend(['mp3', 'opus'] if not self.prefer_opus else ['opus', 'mp3'])

        if not 'media' in data:
            while True:
                try:
                    data = await self._get_track(data['id'])
                    break
                except aiohttp.ContentTypeError:
                    print(f"track {data['id']} couldn't be resolved, retrying in 10 seconds")
                    await asyncio.sleep(10)

        directory = f"{self.directory}/{subdir}"
        os.makedirs(directory, exist_ok=True)
        if track: zfill_track = f"{track[0]:0>{len(str(track[1]))}}"
        
        if data.get("downloadable") and data.get("has_downloads_left") and self.download_original:
            url = f"https://api-v2.soundcloud.com/tracks/{data['id']}/download"
            token = data.get('secret_token')
            if token: url += f"?secret_token={token}"

            async with self._session.get(url, params={'client_id': self._client_id}) as r:
                url = (await r.json())['redirectUri']
            codec = 'original'
        else:
            transcodes = data['media']['transcodings']
            if not transcodes:
                print(f"{data['title']} has no streams")
                return
            for codec in codecs:
                hehe = [x for x in transcodes if x['preset'].partition('_')[0] == codec]
                if not hehe: continue

                try:
                    owo = next(x for x in hehe if x['format']['protocol'] == 'progressive')
                    hls = False
                except StopIteration:
                    owo = next(x for x in hehe if x['format']['protocol'] == 'hls')
                    hls = True
                break            
            try:
                while True:
                    async with self._session.get(owo['url'], params={'client_id': self._client_id}) as r:
                        if r.status == 429:
                            print(f"{data['title']} - TIMEOUT, retrying in half a minute")
                            await asyncio.sleep(30)
                            continue
                        elif r.status == 200:
                            url = (await r.json())['url']
                            break
                        else:
                            print(f"{data['title']} - STATUS CODE: {r.status}, retrying in 10 seconds")
                            await asyncio.sleep(10)
                            continue
            except UnboundLocalError:
                print(track[0])
                raise
        
        lossless = False
        og_codec = None
        if codec == 'original':
            tempfile = utils.get_tempfile('scdl-')
            headers = await self._download_file(url, tempfile)

            proc = await asyncio.subprocess.create_subprocess_exec(
                'ffprobe', tempfile, '-print_format', 'json', '-show_streams',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL
            )

            stdout = (await proc.communicate())[0].decode()
            stream = next(x for x in json.loads(stdout)['streams'] if x['codec_type'] == 'audio')
            og_codec = stream['codec_name']
            lossless = bool(re.fullmatch(_LOSSLESS_REGEX, stream['codec_name']))

            # files user doesn't want to process or files i simply didn't implement processing for
            if not self.process_original or (not og_codec in _EXT_MAP and not lossless):
                path = utils.unique_path(f"{directory}/{f'{zfill_track} - ' if track else ''}{utils.fix_fn(data['title'] + '.' + headers['x-amz-meta-file-type'])}")
                shutil.move(tempfile, path)
                print(f"{data['title']} ({og_codec})")
                return True

            if lossless:
                #print(f"converting {data['title']} to flac")
                file = f'{tempfile}.flac'
                proc = await asyncio.subprocess.create_subprocess_exec(
                    'ffmpeg', '-nostdin', '-i', tempfile, "-vn", "-compression_level", str(self.compression_level), file,
                    stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL
                )
                await proc.wait()
                codec = 'flac'
            else:
                codec = og_codec
                file = f"{tempfile}.{_EXT_MAP[codec]}"
                # deadass saw a track where the original file was a 154 MB mp4 with a visualizer so yeah, -vn
                proc = await asyncio.subprocess.create_subprocess_exec(
                    'ffmpeg', '-nostdin', '-i', tempfile, '-c', 'copy', "-vn", file,
                    stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL
                )
                await proc.wait()
            os.remove(tempfile)
        elif not hls:
            tempfile = utils.get_tempfile('scdl-')
            await self._download_file(url, tempfile)

            file = f"{tempfile}.{_EXT_MAP[codec]}"
            # the below is just in case X could happen (idk what but whateva)
            proc = await asyncio.subprocess.create_subprocess_exec(
                'ffmpeg', '-nostdin', '-i', tempfile, '-c', 'copy', file,
                stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            os.remove(tempfile)
        else:
            async with self._session.get(url) as r:
                playlist = await r.text()
            urls = re.findall(r'https://[^"\n]+', playlist)
                
            tasks = set()
            tfiles = []
            for seg_url in urls:
                if len(tasks) >= _CONCURRENT_SEGMENTS:
                    _, tasks = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                tempfile = utils.get_tempfile('scdl-')
                tfiles.append(tempfile)
                tasks.add(asyncio.create_task(self._download_file(seg_url, tempfile)))
            await asyncio.wait(tasks)
                
            # concat all segments
            file = utils.get_tempfile('scdl-', f".{_EXT_MAP[codec]}")
            proc = await asyncio.subprocess.create_subprocess_exec(
                'ffmpeg', '-nostdin', '-i', f"concat:{'|'.join(tfiles)}", '-c', 'copy', file,
                stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            for tfile in tfiles:
                os.remove(tfile)
        await self._tag(file, data, album, album_artist, track)
        shutil.move(file, utils.unique_path(
            f"{directory}/{f'{zfill_track} - ' if track else ''}{utils.fix_fn(data['title'] + '.' + _EXT_MAP[codec])}"
        ))
        print(f"{data['title']} ({f'{og_codec}->{codec}' if lossless else f'direct-dl {og_codec}' if og_codec else codec})")
        return True

    async def _download_playlist(self, data: dict):
        album_artist = data['user']['username']
        album = data['title']
        subdir = os.path.basename(utils.unique_path(f"{self.directory}/{utils.fix_fn(f'{album_artist} - {album}')}", False))

        tasks = set()
        for i, track_data in enumerate(data['tracks'], 1):
            if len(tasks) >= _CONCURRENT_TRACKS:
                _, tasks = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
            tasks.add(asyncio.create_task(
                self._download_track(track_data, subdir, album, album_artist, (i, len(data['tracks'])))
            ))
        if data['artwork_url']:
            cover_url = await self._get_cover_url(data)
            tasks.add(asyncio.create_task(
                self._download_file(cover_url, f"{self.directory}/{subdir}/cover.{cover_url.rpartition('.')[-1]}")
            ))
        await asyncio.wait(tasks)
    
    async def _collection_gen(self, url: str):
        while True:
            async with self._session.get(url, params={'client_id': self._client_id}) as r:
                data = await r.json()
                if not data['next_href']:
                    return
                
                for track in data['collection']:
                    yield track
                
                url = data['next_href'].replace('://http_backend/', '://api-v2.soundcloud.com/', 1)

    async def _download_collection(self, data: dict, type: str = 'user') -> None:
        match type:
            case 'user':
                url = f"https://api-v2.soundcloud.com/users/{data['id']}/tracks?limit=100"
                subdir = data['username']
            case 'reposts':
                url = f"https://api-v2.soundcloud.com/stream/users/{data['id']}/reposts?representation=&limit=100"
                subdir = data['username'] + ' - reposts'
            case 'likes':
                url = f"https://api-v2.soundcloud.com/users/{data['id']}/likes?representation=&limit=100"
                subdir = data['username'] + ' - likes'
            case _:
                raise ValueError(f"'{type}' is not a valid/supported collection type")
        subdir = os.path.basename(utils.unique_path(f"{self.directory}/{utils.fix_fn(subdir)}", False))
        
        tasks = set()
        i = 1
        async for track_data in self._collection_gen(url):
            if type != 'user':
                if not 'track' in track_data:
                    continue
                track_data = track_data['track']
            if len(tasks) >= _CONCURRENT_TRACKS:
                _, tasks = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
            tasks.add(asyncio.create_task(
                self._download_track(track_data, subdir)
            ))
        await asyncio.wait(tasks)

    async def download(self, url: str) -> None:
        if not self._session:
            raise SCSessionClosedError("soundcloud session wasn't opened, use 'async with' construct")
        
        url = await self._clean_url(url)
        print(f"\ndownloading {url}")
        resolved = {}
        link_type = self._get_link_type(url)
        match link_type:
            case "user" | "likes" | "reposts":
                # user url without /likes and etc following it
                owo = re.match(r"https://soundcloud.com/[^/]+", url).group(0)
                resolved = await self._resolve_url(owo)
            case "track" | "playlist":
                resolved = await self._resolve_url(url)

        if not resolved:
            raise SCIncorrectUrlException(f"{url} could not be resolved, is it correct?")
        
        match link_type:
            case "track":
                await self._download_track(resolved)
            case "playlist":
                await self._download_playlist(resolved)
            case "user" | "reposts" | "likes":
                await self._download_collection(resolved, link_type)

#---------------------------------------------------------#

async def _cli(argv: list) -> None:
    scdl = SoundCloudDL()
    cfg = config.get_config()
    
    parser = ArgumentParser()
    parser.add_argument('url', nargs='+', type=str)
    parser.add_argument('-o', '--directory', type=str, help='download directory')
    parser.add_argument('-a', '--oauth-token', type=str, help='account token; format: X-XXXXXX-XXXXXXXXXX-XXXXXXXXXXXXX')
    parser.add_argument('-O', '--prefer-opus', action='store_true', help="prefer 64 kbps opus over 128 kbps mp3")
    parser.add_argument('-m', '--prefer-mp3', action='store_true', help="prefer 128 kbps mp3 over 64 kbps opus (default)")
    parser.add_argument('-H', '--high-quality', action='store_true', help="download aac if available (default)")
    parser.add_argument('-l', '--low-quality', action='store_true', help="never download aac")
    parser.add_argument('-d', '--download-original', action='store_true', help="download original files if available (default)")
    parser.add_argument('-D', '--dont-download-original', action='store_true', help="never download original files")
    parser.add_argument('-p', '--process-original', action='store_true', help="convert lossless to flac and tag original files (default)")
    parser.add_argument('-P', '--dont-process-original', action='store_true', help="leave original files untouched")
    parser.add_argument('-c', '--compression-level', type=int, choices=[x for x in range(13)], help='flac compression level (default = 12)')
    args = parser.parse_args(argv)
    
    scdl.directory = args.directory if args.directory else cfg['directory']
    scdl.oauth_token = args.oauth_token if args.oauth_token else cfg['oauth_token']
    scdl.compression_level = args.compression_level if args.compression_level else cfg['compression_level']

    # awful part i should probably improve but ioncare
    if args.prefer_opus:
        if args.prefer_mp3:
            print('error: both --prefer-opus and --prefer-mp3 were specified, quitting')
            return
        scdl.prefer_opus = True
    elif args.prefer_mp3:
        scdl.prefer_opus = False
    else:
        scdl.prefer_opus = cfg['prefer_opus']
    
    if args.low_quality:
        if args.high_quality:
            print('error: both --high-quality and --low-quality were specified, quitting')
            return
        scdl.low_quality = True
    elif args.high_quality:
        scdl.low_quality = False
    else:
        scdl.low_quality = cfg['prefer_opus']

    if args.download_original:
        if args.dont_download_original:
            print('error: both --download-original and --dont-download-original were specified, quitting')
            return
        scdl.download_original = True
    elif args.dont_download_original:
        scdl.download_original = False
    else:
        scdl.download_original = cfg['download_original']
    
    if args.process_original:
        if args.dont_process_original:
            print('error: both --process-original and --dont-process-original were specified, quitting')
            return
        scdl.process_original = True
    elif args.dont_process_original:
        scdl.process_original = False
    else:
        scdl.process_original = cfg['process_original']
    # awful part over ((relief))

    async with scdl as s:
        for url in args.url:
            try:
                await scdl.download(url)
            except SCIncorrectUrlException as e:
                print(e)

def cli_run(args: list = sys.argv[1:]) -> None:
    asyncio.run(_cli(args))

if __name__ == '__main__':
    try:
        cli_run()
    except KeyboardInterrupt:
        pass

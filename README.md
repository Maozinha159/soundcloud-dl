# soundcloud-dl
An advanced asynchronous SoundCloud downloader capable of ripping 256 kbps aac and downloading original files if possible (with converting lossless to flac).

# Windows note
While it has been mostly tested on linux, it should work on Windows just fine except of RuntimeError printed after finishing, which is caused by either aiohttp or asyncio. A workaround for that is most likely possible, but I don't care enough to fix a minor bug affecting a platform anyone competent (which anyone using this script would already be) shouldn't use.

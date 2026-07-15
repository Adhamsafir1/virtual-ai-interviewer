import socket
import asyncio
import aiohttp

old_getaddrinfo = socket.getaddrinfo
def new_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    family = socket.AF_INET
    return old_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = new_getaddrinfo

async def main():
    async with aiohttp.ClientSession() as session:
        print("Testing aiohttp dns...")
        async with session.get('https://api.deepgram.com/') as resp:
            print("Status:", resp.status)

asyncio.run(main())

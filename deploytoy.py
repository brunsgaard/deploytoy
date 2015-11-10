import asyncio
from aiohttp import web
import hmac
import hashlib
import logging
import signal
from functools import partial
from os.path import dirname, realpath, join
import os
import sys

logger = logging.getLogger('deploytoy')

secret = sys.argv[1].encode()
enforce_secret = False

queue = asyncio.Queue()
handlers = {}
asyncio.Lock()


def eventhandler(f):
    handlers[f.__name__] = f
    return f


async def handle_web_request(request):

    if enforce_secret:
        # may throw HTTPexception 401
        await verify_request_sender_as_github(request)

    try:
        event = request.headers['X-Github-Event']
    except KeyError:
        # status code is 400
        raise web.HTTPBadRequest(
            reason="'X-Github-Event' header missing")

    # TODO
    request.headers.get('X-Github-Delivery')

    await queue.put((event, request))
    return web.Response(status=201)


async def verify_request_sender_as_github(request):
    """
    Verify based on global secret, that request was actually signed by github
    """
    try:
        data = await request.read()
        if not hmac.compare_digest(
                request.headers['X-HUB-SIGNATURE'][5:],
                hmac.new(secret, data, hashlib.sha1).hexdigest()):
            raise
    except Exception as e:
        logger.info('Unauthorized request\n {}'.format(e))
        raise web.HTTPUnauthorized


async def queue_executer():
    while True:
        event, request = await queue.get()
        if event is None:
            break
        try:
            f = handlers[event]
            await asyncio.coroutine(f)(request)
        except:
            logger.exception('qe somethign went wrong')
            continue


def close(handler, app, server, loop):
    async def close_():
        await handler.finish_connections(1.0)
        server.close()
        await server.wait_closed()
        await queue.put((None, None))
        await app.finish()
        loop.stop()
        logger.debug('Deploytoy stopped gracefully')
    asyncio.ensure_future(close_())


async def run(loop):

    queue_executer_closed = asyncio.ensure_future(queue_executer())
    async def wait_for_executer(_):
        await asyncio.wait_for(queue_executer_closed, None)

    app = web.Application(loop=loop)
    app.router.add_route('POST', '/', handle_web_request)
    app.register_on_finish(wait_for_executer)
    handler = app.make_handler()
    server = await asyncio.ensure_future(loop.create_server(
        handler, '0.0.0.0', 5000))

    loop.add_signal_handler(
        signal.SIGTERM, partial(close, handler, app, server, loop))
    loop.add_signal_handler(
        signal.SIGINT, partial(close, handler, app, server, loop))
    logger.debug('Deploytoy has started')


@eventhandler
async def push(request):
    payload = await request.json()

    repository = payload['repository']['name']
    branch = payload['ref'].split('/')[2]
    prefix = '{}-push-{}'.format(repository, branch)

    hookdir = join(dirname(realpath(__file__)), 'hooks')
    scripts = [f for f in os.listdir(hookdir) if f.startswith(prefix)]

    if len(scripts) != 1:
        logger.warning('Exactly one script should match the prefix pattern')
        return
    else:
        script = join(hookdir, scripts[0])

    proc = await asyncio.create_subprocess_exec(
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True
    )

    try:
        await asyncio.wait_for(proc.communicate(), 5*60)
    except asyncio.TimeoutError:
        proc.kill()
        logger.critical('Script timed out and was killed')

    if proc.returncode != 0:
        stdout = await proc.stdout.read()
        stdout = stdout.decode().strip()
        if stdout:
            logger.critical(stdout)


if __name__ == '__main__':

    logger.setLevel(logging.DEBUG)

    handler = logging.FileHandler('deploytoy.log')
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    loop = asyncio.get_event_loop()
    asyncio.ensure_future(run(loop))
    try:
        loop.run_forever()
    finally:
        loop.close()

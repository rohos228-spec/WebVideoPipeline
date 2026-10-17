import asyncio

from app.billing import limits


def test_slot_waits_then_runs():
    async def main():
        order = []

        async def job(i):
            async with limits.slot("free-user", "video", f"a{i}"):
                order.append(("start", i))
                await asyncio.sleep(0.01)
                order.append(("end", i))

        await asyncio.gather(job(1), job(2))
        return order

    order = asyncio.run(main())
    assert order.index(("end", 1)) < order.index(("start", 2))

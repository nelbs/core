"""Test cases that are in common among wemo platform modules.

This is not a test module. These test methods are used by the platform test modules.
"""
import asyncio
import threading

from homeassistant.components.homeassistant import DOMAIN as HA_DOMAIN
from homeassistant.components.wemo import wemo_device
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.setup import async_setup_component


def _perform_registry_callback(coordinator):
    """Return a callable method to trigger a state callback from the device."""

    async def async_callback():
        await coordinator.hass.async_add_executor_job(
            coordinator.subscription_callback, coordinator.wemo, "", ""
        )

    return async_callback


def _perform_async_update(coordinator):
    """Return a callable method to cause hass to update the state of the entity."""

    async def async_callback():
        await coordinator._async_update_data()

    return async_callback


async def _async_multiple_call_helper(hass, pywemo_device, call1, call2):
    """Create two calls (call1 & call2) in parallel; verify only one polls the device.

    There should only be one poll on the device at a time. Any parallel updates
    # that happen at the same time should be ignored. This is verified by blocking
    in the get_state method. The polling method should only be called once as a
    result of calling call1 & call2 simultaneously.
    """
    event = threading.Event()
    waiting = asyncio.Event()
    call_count = 0

    def get_state(force_update=None):
        if force_update is None:
            return
        nonlocal call_count
        call_count += 1
        hass.loop.call_soon_threadsafe(waiting.set)
        event.wait()

    # Danger! Do not use a Mock side_effect here. The test will deadlock. When
    # called though hass.async_add_executor_job, Mock objects !surprisingly!
    # run in the same thread as the asyncio event loop.
    # https://github.com/home-assistant/core/blob/1ba5c1c9fb1e380549cb655986b5f4d3873d7352/tests/common.py#L179
    pywemo_device.get_state = get_state

    # One of these two calls will block on `event`. The other will return right
    # away because the `_update_lock` is held.
    done, pending = await asyncio.wait(
        [call1(), call2()], return_when=asyncio.FIRST_COMPLETED
    )
    _ = [d.result() for d in done]  # Allow any exceptions to be raised.

    # Allow the blocked call to return.
    await waiting.wait()
    event.set()

    if pending:
        done, _ = await asyncio.wait(pending)
        _ = [d.result() for d in done]  # Allow any exceptions to be raised.

    # Make sure the state update only happened once.
    assert call_count == 1


async def test_async_update_locked_callback_and_update(
    hass, pywemo_device, wemo_entity
):
    """Test that a callback and a state update request can't both happen at the same time.

    When a state update is received via a callback from the device at the same time
    as hass is calling `async_update`, verify that only one of the updates proceeds.
    """
    coordinator = wemo_device.async_get_coordinator(hass, wemo_entity.device_id)
    await async_setup_component(hass, HA_DOMAIN, {})
    callback = _perform_registry_callback(coordinator)
    update = _perform_async_update(coordinator)
    await _async_multiple_call_helper(hass, pywemo_device, callback, update)


async def test_async_update_locked_multiple_updates(hass, pywemo_device, wemo_entity):
    """Test that two hass async_update state updates do not proceed at the same time."""
    coordinator = wemo_device.async_get_coordinator(hass, wemo_entity.device_id)
    await async_setup_component(hass, HA_DOMAIN, {})
    update = _perform_async_update(coordinator)
    await _async_multiple_call_helper(hass, pywemo_device, update, update)


async def test_async_update_locked_multiple_callbacks(hass, pywemo_device, wemo_entity):
    """Test that two device callback state updates do not proceed at the same time."""
    coordinator = wemo_device.async_get_coordinator(hass, wemo_entity.device_id)
    await async_setup_component(hass, HA_DOMAIN, {})
    callback = _perform_registry_callback(coordinator)
    await _async_multiple_call_helper(hass, pywemo_device, callback, callback)


async def test_avaliable_after_update(
    hass, pywemo_registry, pywemo_device, wemo_entity, domain
):
    """Test the avaliability when an On call fails and after an update.

    This test expects that the pywemo_device Mock has been setup to raise an
    ActionException when the SERVICE_TURN_ON method is called and that the
    state will be On after the update.
    """
    await async_setup_component(hass, domain, {})

    await hass.services.async_call(
        domain,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: [wemo_entity.entity_id]},
        blocking=True,
    )
    assert hass.states.get(wemo_entity.entity_id).state == STATE_UNAVAILABLE

    pywemo_registry.callbacks[pywemo_device.name](pywemo_device, "", "")
    await hass.async_block_till_done()
    assert hass.states.get(wemo_entity.entity_id).state == STATE_ON

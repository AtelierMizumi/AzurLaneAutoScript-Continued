import time

import numpy as np

from module.base.timer import Timer
from module.fleet_scheduler import assets as fleet_assets
from module.logger import logger
from module.ocr.ocr import DigitCounter, Ocr
from module.retire.assets import DOCK_CHECK, DOCK_SELECTED
from module.retire.scanner import ShipScanner

# "Selected: N/M" counter of the placement picker; same dock UI position
# as the retire module's selected counter.
DORM_COUNTER_OCR = DigitCounter(DOCK_SELECTED, threshold=64, name='DORM_COUNTER')
# "Lv.###" of each slot card; empty result = empty or locked slot.
DORM_SLOT_LV_OCR = [
    Ocr(area, alphabet='0123456789', name=f'DORM_SLOT_LV_{k}')
    for k, area in enumerate(fleet_assets.DORM_SLOT_LV_AREAS, start=1)
]


class DormPlacement:
    """
    Sync the dorm tab selection with a target fleet's members. Azdaily
    fork.

    The placement picker is the standard dock in multi-select mode; its
    "Selected: N/M" counter updates on every card tap, which turns every
    toggle into a verified operation: tap a card, re-read the counter,
    +1 means added, -1 means the card was already placed (tap again to
    restore). Members are recognised by their fleet hexagon chip
    (FleetScanner); non-member occupants to evict are matched through the
    level shown on the slot cards. Floors are synced one by one; a locked
    slot simply fails to open the picker and is skipped.
    """

    DORM_SLOT_COUNT = 6
    DORM_TOGGLE_LIMIT = 16
    DORM_ROW_STEPS_LIMIT = 16
    DORM_ROW_HEIGHT = 227
    DORM_GRID_DRAG_UP = ((640, 400), (640, 400 - 227))

    def dorm_tab_active(self, mode):
        strip = fleet_assets.DORM_TAB_STRIP_TRAIN if mode == 'train' else fleet_assets.DORM_TAB_STRIP_REST
        return self.image_color_count(strip, color=(255, 213, 82), threshold=45, count=100)

    def dorm_ensure_tab(self, mode):
        click_timer = Timer(1.5, count=3)
        for _ in self.loop(timeout=8):
            if self.dorm_tab_active(mode):
                return True
            if click_timer.reached():
                tab = fleet_assets.DORM_TAB_TRAIN if mode == 'train' else fleet_assets.DORM_TAB_REST
                self.device.click(tab)
                click_timer.reset()
        logger.warning(f'DormFill: {mode} tab did not activate')
        return False

    def dorm_slot_occupants(self):
        """
        Returns:
            dict[int, int or None]: Slot index -> occupant level. None on
                empty or locked slots (no "Lv.###" on the card).
        """
        self.device.screenshot()
        occupants = {}
        for k, ocr in enumerate(DORM_SLOT_LV_OCR, start=1):
            if k > self.DORM_SLOT_COUNT:
                break
            try:
                level = ocr.ocr(self.device.image)
            except Exception:
                level = None
            occupants[k] = int(level) if level else None
        logger.attr('Dorm slots', occupants)
        return occupants

    def _dorm_read_counter(self):
        """
        Returns:
            tuple[int, int] or None: (selected, slots) from the picker
                counter, None when unreadable.
        """
        self.device.screenshot()
        try:
            result = DORM_COUNTER_OCR.ocr(self.device.image)
        except Exception:
            return None
        if isinstance(result, (tuple, list)) and len(result) == 2:
            return int(result[0]), int(result[1])
        if isinstance(result, int):
            return result, None
        return None

    def _dorm_card_tap(self, button):
        """
        Tap a picker card and classify the effect through the counter.

        Returns:
            str: 'added', 'removed', 'unchanged' (tap missed), or
                'unknown' (counter unreadable).
        """
        before = self._dorm_read_counter()
        self.device.click(button)
        after = self._dorm_read_counter()
        if before is None or after is None or before[0] is None or after[0] is None:
            return 'unknown'
        if after[0] > before[0]:
            return 'added'
        if after[0] < before[0]:
            return 'removed'
        return 'unchanged'

    def _dorm_scan_view(self, scanner):
        self.device.screenshot()
        return scanner._scan(self.device.image)

    @staticmethod
    def _dorm_signature(ships):
        return [(s.level, s.emotion, s.rarity, s.fleet) for s in ships]

    def _dorm_sync_floor(self, fleet):
        """
        One picker session on the current floor: tap every member card
        (adds unplaced members, restores accidentally toggled placed
        ones) and every non-member occupant card (evicts them), then
        confirm once. The view is re-scanned after every tap because the
        picker may re-sort.

        Args:
            fleet (int): Target fleet number.

        Returns:
            int or None: Amount of member ships newly placed, None when
                the picker could not be opened.
        """
        occupants = self.dorm_slot_occupants()
        empties = [s for s, lv in occupants.items() if lv is None]
        logger.attr('Dorm empty slots', empties)
        if not empties:
            logger.info('DormFill: floor has no empty slot, the picker cannot open; '
                        'free one slot manually once and the sync will keep it synced')
            return None
        if not self._enter_slot_dock(getattr(fleet_assets, f'DORM_SLOT_{empties[0]}'), timeout=15):
            logger.warning('DormFill: picker did not open from the empty slot')
            return None

        # No faction/level filter: the selection mirrors the fleet exactly.
        self.dock_favourite_set(False, wait_loading=False)
        self.dock_sort_method_dsc_set(True, wait_loading=False)
        self.dock_filter_set(sort='level', extra='not_level_max')
        scanner = ShipScanner(level=(1, 125), emotion=(0, 150), fleet=None, status='free')

        occupant_levels = {lv for lv in occupants.values() if lv is not None}
        placed = 0
        toggles = 0
        # Levels already settled (member placed or restored, occupant
        # evicted, or a tap that missed) — the picker re-sorts on every
        # toggle, so without this the same card would be tapped forever.
        skip_levels = set()
        previous = None
        steps = 0
        while 1:
            raw = self._dorm_scan_view(scanner)
            if previous is not None and \
                    self._dorm_signature(raw) == self._dorm_signature(previous):
                logger.info('DormFill: dock bottom reached')
                break
            previous = raw
            actionable = None
            intent = None
            for ship in raw:
                if ship.level in skip_levels:
                    continue
                if ship.fleet == fleet:
                    actionable, intent = ship, 'member'
                    break
                if ship.level in occupant_levels:
                    actionable, intent = ship, 'evict'
                    break
            if actionable is None:
                steps += 1
                if steps >= self.DORM_ROW_STEPS_LIMIT:
                    logger.warning('DormFill: dock survey row limit reached')
                    break
                p1, p2 = self.DORM_GRID_DRAG_UP
                self.device.drag(p1, p2, point_random=(0, 0, 0, 0), hold_duration=0.15,
                                 name='DOCK_GRID_DRAG')
                scanner.move((0, -self.DORM_ROW_HEIGHT))
                continue

            effect = self._dorm_card_tap(actionable.button)
            toggles += 1
            if intent == 'member':
                if effect == 'added':
                    placed += 1
                    logger.info(f'DormFill: member added, level {actionable.level}')
                elif effect == 'removed':
                    # Already placed; restore.
                    self.device.click(actionable.button)
                    logger.attr('DormFill: member was already placed, level', actionable.level)
                elif effect == 'unknown':
                    logger.warning('DormFill: counter unreadable, stop toggling')
                    break
            else:
                if effect == 'removed':
                    logger.info(f'DormFill: occupant evicted, level {actionable.level}')
                elif effect == 'added':
                    # Not an occupant after all; revert.
                    self.device.click(actionable.button)
                    logger.info('DormFill: evict tap selected a non-occupant, reverted')
                elif effect == 'unknown':
                    logger.warning('DormFill: counter unreadable, stop toggling')
                    break
            skip_levels.add(actionable.level)
            if toggles >= self.DORM_TOGGLE_LIMIT:
                logger.warning(f'DormFill: toggle limit {self.DORM_TOGGLE_LIMIT} reached')
                break

        self.dock_select_confirm(check_button=fleet_assets.DORM_CHECK)
        return placed

    def _dorm_switch_floor(self, occupants_before):
        """
        Tap the floor swap icon and verify the floor actually changed by
        the slot occupants being different.

        Args:
            occupants_before (dict[int, int or None]): Occupants of the
                current floor.

        Returns:
            bool: True if the floor changed.
        """
        for _ in range(2):
            self.device.click(fleet_assets.DORM_FLOOR_SWAP)
            time.sleep(1.5)
            if self.dorm_slot_occupants() != occupants_before:
                logger.info('DormFill: switched to the next floor')
                return True
        logger.info('DormFill: floor did not change, stop at this floor')
        return False

    def dorm_fill(self, mode, fleet, limit=None):
        """
        Sync the dorm tab selection with the target fleet's members, one
        floor at a time.

        Args:
            mode (str): 'train' or 'rest', which tab to sync.
            fleet (int): Target fleet number; 0 aborts.
            limit (int or None): Place at most this many member ships.

        Returns:
            bool: True if any member ship has been placed.
        """
        if not fleet:
            logger.info('DormFill: no target fleet, skip')
            return False
        logger.hr(f'Dorm fill ({mode}, fleet {fleet})', level=1)
        if not self.dorm_ensure_tab(mode):
            return False
        placed_total = 0
        for floor in (1, 2):
            if limit is not None and placed_total >= limit:
                break
            try:
                placed = self._dorm_sync_floor(fleet)
            except Exception as e:
                logger.warning(f'DormFill error on floor {floor}: {e}')
                if self.appear(DOCK_CHECK, offset=(20, 20)):
                    self.dock_quit()
                break
            if placed is None:
                break
            placed_total += placed
            if placed == 0:
                break
            if not self._dorm_switch_floor(occupants_before=self.dorm_slot_occupants()):
                break
        logger.info(f'DormFill: placed {placed_total} member ship(s)')
        return placed_total > 0

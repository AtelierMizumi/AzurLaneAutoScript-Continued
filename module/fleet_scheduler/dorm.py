import numpy as np

from module.base.timer import Timer
from module.base.utils import area_offset, rgb2gray
from module.fleet_scheduler import assets as fleet_assets
from module.logger import logger
from module.retire.assets import DOCK_CHECK
from module.retire.scanner import ShipScanner


class DormPlacement:
    """
    Fill empty slots of the reworked dorm (Train / Rest tabs) with
    rotation candidates. Azdaily fork.

    The placement picker is the standard dock in multi-select mode: ships
    already placed in this tab's slots show "- SELECTED -", ships resting
    in the other tab show a "Resting" label — both must never be touched,
    so candidates are bench ships only (no fleet badge) that carry neither
    label. Fleet members keep their assignment while training/resting, but
    v1 leaves them alone; resting the farm fleets stays manual.
    """

    DORM_SLOT_COUNT = 5
    # Flatness of a slot card below this standard deviation is an empty
    # (or locked) slot frame; occupied cards are artwork (std > 30).
    DORM_EMPTY_STD = 12
    # Training / Resting label on a ship card, cell-relative.
    DORM_LABEL_ZONE = (0, 126, 102, 156)

    def dorm_tab_active(self, mode):
        """
        Args:
            mode (str): 'train' or 'rest'.

        Returns:
            bool: True if the tab's yellow underline strip is present.
        """
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

    def dorm_empty_slots(self):
        """
        Returns:
            list[int]: 1-based indexes of empty slot cards among the first
                DORM_SLOT_COUNT slots (the locked slot is never included).
        """
        empties = []
        for k in range(1, self.DORM_SLOT_COUNT + 1):
            button = getattr(fleet_assets, f'DORM_SLOT_{k}')
            image = rgb2gray(self.image_crop(button, copy=False))
            std = float(np.std(image.flatten(), ddof=1))
            if std < self.DORM_EMPTY_STD:
                empties.append(k)
        logger.attr('Dorm empty slots', empties)
        return empties

    def _dorm_ship_in_dorm(self, ship):
        """
        Args:
            ship (Ship): A scanned dock card.

        Returns:
            bool: True if the card carries a Training / Resting label,
                i.e. the ship already occupies a dorm slot.
        """
        area = area_offset(self.DORM_LABEL_ZONE, offset=ship.button.area[:2])
        crop = self.image_crop(area, copy=False)
        for template in (fleet_assets.TEMPLATE_DORM_TRAINING, fleet_assets.TEMPLATE_DORM_RESTING):
            if template.match(crop, similarity=0.8):
                return True
        return False

    def dorm_fill(self, mode, factions, level_max, limit=None):
        """
        Fill empty dorm slots with rotation candidates.

        Args:
            mode (str): 'train' or 'rest', which tab to fill.
            factions (list[str] or None): Dock filter factions of the
                rotation target; None aborts.
            level_max (int): Candidates must be below this level, so the
                dorm grows ships toward the rotation band.
            limit (int or None): Fill at most this many slots.

        Returns:
            bool: True if any slot has been filled.
        """
        if not factions:
            logger.info('DormFill: no faction source (research target unread or done), skip')
            return False
        logger.hr(f'Dorm fill ({mode})', level=1)
        if not self.dorm_ensure_tab(mode):
            return False
        empties = self.dorm_empty_slots()
        if not empties:
            logger.info('DormFill: no empty slot')
            return False
        filled = 0
        for slot in (empties[:limit] if limit else empties):
            try:
                if self._dorm_place_one(slot, mode, factions, level_max):
                    filled += 1
            except Exception as e:
                logger.warning(f'DormFill error on slot {slot}: {e}')
                if self.appear(DOCK_CHECK, offset=(20, 20)):
                    self.dock_quit()
                break
        logger.info(f'DormFill: filled {filled} slot(s)')
        return filled > 0

    def _dorm_place_one(self, slot, mode, factions, level_max):
        """
        Fill one empty slot through the placement picker (the standard
        dock; the picker must be closed on every return path).

        Args:
            slot (int): 1-based slot index to fill.

        Returns:
            bool: True if the slot flipped from empty to occupied.
        """
        from module.fleet_scheduler.scheduler import match_record, pick_candidate_record

        button = getattr(fleet_assets, f'DORM_SLOT_{slot}')
        if not self._enter_slot_dock(button, timeout=15):
            logger.warning(f'DormFill: picker did not open from slot {slot}')
            return False
        self.dock_favourite_set(False, wait_loading=False)
        self.dock_sort_method_dsc_set(True, wait_loading=False)
        self.dock_filter_set(sort='level', faction=factions, extra='not_level_max')

        scanner_kwargs = dict(level=(1, level_max), emotion=(0, 150), fleet=None, status='free')
        scanner = ShipScanner(**scanner_kwargs)
        records, last_page_ships, pages = self._dock_survey(scanner)
        candidates = [record for record in records
                      if record.ship.fleet == 0 and not self._dorm_ship_in_dorm(record.ship)]
        if not candidates:
            logger.info('DormFill: no bench candidate of the target factions')
            self.dock_reset()
            self.dock_quit()
            return False

        # Highest level first: ships closest to the rotation band graduate
        # out of the dorm soonest.
        record = pick_candidate_record(candidates, descending=True)
        ship = record.ship
        logger.info(f'DormFill: select candidate level {ship.level}, emotion {ship.emotion}, '
                    f'page {record.page + 1}/{pages}')

        if record.page == pages - 1:
            target = record.ship
        else:
            fresh = ShipScanner(**scanner_kwargs)
            ships = self._dock_relocate_scan(record.page, fresh)
            target = match_record(ships, record) if ships is not None else None
            if target is not None and self._dorm_ship_in_dorm(target):
                logger.warning('DormFill: fresh candidate view carries a dorm label, skip')
                target = None
        if target is None:
            self.dock_reset()
            self.dock_quit()
            return False

        self.dock_select_one(target.button)
        self.dock_reset()
        self.dock_select_confirm(check_button=fleet_assets.DORM_CHECK)

        if slot in self.dorm_empty_slots():
            logger.warning(f'DormFill: slot {slot} still empty after confirm')
            return False
        logger.info(f'DormFill: slot {slot} filled')
        return True

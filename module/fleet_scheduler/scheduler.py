import numpy as np

from module.base.timer import Timer
from module.base.utils import rgb2gray
from module.config.deep import deep_get
from module.equipment import assets as equipment_assets
from module.fleet_scheduler import assets as fleet_assets
from module.logger import logger
from module.map.assets import FLEET_PREPARATION
from module.retire.assets import DOCK_CHECK
from module.retire.dock import Dock
from module.retire.scanner import ShipScanner


def _slot_button(fleet_index, suffix):
    """
    Args:
        fleet_index (int): 1 or 2.
        suffix (str): One of BACKLINE_1, BACKLINE_2, BACKLINE_3,
            VANGUARD_1, VANGUARD_2, VANGUARD_3.

    Returns:
        Button: Click anchor of the fleet slot on the preparation screen.
            Slot 2 comes from module/fleet_scheduler/assets.py (hand-derived),
            slot 1 and 3 from module/equipment/assets.py (upstream).
    """
    try:
        return getattr(equipment_assets, f'FLEET_{fleet_index}_{suffix}')
    except AttributeError:
        return getattr(fleet_assets, f'FLEET_{fleet_index}_{suffix}')


class FleetScheduler(Dock):
    """
    Fill and rotate farm-fleet slots by faction, on the fleet preparation
    screen, for PR/DR research EXP farming.

    Azdaily fork. This runs once per sortie task, hooked in
    FleetPreparation.fleet_preparation(), so the fleets are corrected right
    before battle without any extra navigation. Slot targets live in the
    FleetScheduler task (FarmFleet1 / FarmFleet2 / Rotation groups) and are
    read cross-task from the raw config, because the hook executes under
    another task's config binding.
    """

    # slot arg -> (button suffix on the preparation screen, dock filter index)
    SLOT_PLAN_ARGS = {
        'Backline1': ('BACKLINE_1', 'main'),
        'Flagship': ('BACKLINE_2', 'main'),
        'Backline3': ('BACKLINE_3', 'main'),
        'Vanguard1': ('VANGUARD_1', 'vanguard'),
        'Vanguard2': ('VANGUARD_2', 'vanguard'),
        'Vanguard3': ('VANGUARD_3', 'vanguard'),
    }

    # Flatness of the slot anchor below this standard deviation is considered
    # an empty slot frame (same trick as FleetOperator.in_use, looser bound).
    EMPTY_SLOT_STD = 12

    @classmethod
    def slot_plan(cls, config_data):
        """
        Read slot targets cross-task.

        Args:
            config_data (dict): Full config JSON, e.g. self.config.data.

        Returns:
            dict: {fleet_index: [(slot_arg, faction, row), ...]},
                non-keep slots only. Empty dict when the feature is unused.
        """
        plan = {}
        for fleet_index in (1, 2):
            slots = []
            for arg, (_, row) in cls.SLOT_PLAN_ARGS.items():
                faction = deep_get(
                    config_data, keys=f'FleetScheduler.FarmFleet{fleet_index}.{arg}', default='keep')
                if faction and faction != 'keep':
                    slots.append((arg, faction, row))
            if slots:
                plan[fleet_index] = slots
        return plan

    def apply(self, plan=None):
        """
        Args:
            plan (dict): See slot_plan(). Reads from config when omitted.

        Returns:
            bool: True if any slot has been (re)assigned.
        """
        if plan is None:
            plan = self.slot_plan(self.config.data)
        if not plan:
            return False

        logger.hr('Fleet scheduler', level=1)
        self.handle_info_bar()
        applied = False
        for fleet_index, slots in plan.items():
            for arg, faction, row in slots:
                try:
                    if self.assign_slot(fleet_index=fleet_index, arg=arg, faction=faction, row=row):
                        applied = True
                except Exception as e:
                    logger.warning(f'Fleet scheduler error on Fleet {fleet_index} {arg}: {e}')
                    if self.appear(DOCK_CHECK, offset=(20, 20)):
                        self.dock_quit()
                    logger.info('Skip remaining slots to keep the sortie going')
                    return applied
        return applied

    def assign_slot(self, fleet_index, arg, faction, row):
        """
        Fill or rotate one fleet slot.

        Args:
            fleet_index (int): 1 or 2.
            arg (str): Slot config arg name, e.g. 'Flagship'.
            faction (str): Dock filter faction, e.g. 'iron'.
            row (str): 'main' or 'vanguard'.

        Returns:
            bool: True if the slot has been (re)assigned.
        """
        rotate_level = deep_get(self.config.data, keys='FleetScheduler.Rotation.RotateLevel', default=100)
        level_min = deep_get(self.config.data, keys='FleetScheduler.Rotation.LevelMin', default=1)
        emotion_min = deep_get(self.config.data, keys='FleetScheduler.Rotation.EmotionMin', default=0)
        descending = deep_get(
            self.config.data, keys='FleetScheduler.Rotation.CandidateSort', default='level_asc') == 'level_desc'
        level_max = rotate_level if rotate_level and rotate_level > 0 else 125

        logger.info(f'Fleet scheduler: Fleet {fleet_index} {arg}, faction {faction}, row {row}')
        button = _slot_button(fleet_index, self.SLOT_PLAN_ARGS[arg][0])
        empty = self._slot_is_empty(button)
        logger.attr('Slot empty', empty)

        self._enter_slot_dock(button)
        self.dock_favourite_set(False, wait_loading=False)
        self.dock_sort_method_dsc_set(descending, wait_loading=False)
        self.dock_filter_set(sort='level', index=row, faction=faction, extra='not_level_max')

        # fleet=None: keep fleet badges in the results so ships already in
        # fleets can be told apart from candidates (badge 0).
        scanner = ShipScanner(
            level=(level_min, level_max), emotion=(emotion_min, 150), fleet=None, status='free')
        ships = scanner.scan(self.device.image, output=False)
        candidates = [ship for ship in ships if ship.fleet == 0]
        in_farm = [ship for ship in ships if ship.fleet == fleet_index]

        if not empty and in_farm and (not rotate_level or rotate_level <= 0
                                      or max(ship.level for ship in in_farm) < rotate_level):
            logger.info(f'Keep slot, fleet {fleet_index} {row} ships of faction {faction}: '
                        f'{[ship.level for ship in in_farm]} all below {rotate_level}')
            self.dock_reset()
            self.dock_quit()
            return False

        if not candidates:
            logger.warning(f'No candidate for faction {faction}, row {row}, '
                           f'level {level_min}-{level_max}, emotion >= {emotion_min}')
            self.dock_reset()
            self.dock_quit()
            return False

        if descending:
            ship = max(candidates, key=lambda s: (s.level, s.emotion))
        else:
            ship = min(candidates, key=lambda s: (s.level, -s.emotion))
        logger.info(f'Select candidate: level {ship.level}, emotion {ship.emotion}, rarity {ship.rarity}')
        self.dock_select_one(ship.button)
        self.dock_reset()
        self.dock_select_confirm(check_button=FLEET_PREPARATION)
        logger.info(f'Assign Fleet {fleet_index} {arg} success')
        return True

    def _slot_is_empty(self, button):
        """
        Args:
            button (Button): Slot anchor on the preparation screen.

        Returns:
            bool: True if the slot anchor is a flat frame, i.e. no ship.
                A false "occupied" only skips a fill and is logged by the
                candidate scan, a false "empty" is harmless (the dock scan
                still decides against a needless swap).
        """
        image = rgb2gray(self.image_crop(button, copy=False))
        std = np.std(image.flatten(), ddof=1)
        logger.attr(f'{button} std', round(float(std), 1))
        return std < self.EMPTY_SLOT_STD

    def _enter_slot_dock(self, button):
        """
        Click a fleet slot on the preparation screen until the dock opens.
        """
        logger.info(f'Enter dock from slot {button}')
        self.interval_clear(DOCK_CHECK)
        click_timer = Timer(3, count=6)
        for _ in self.loop(timeout=30):
            if self.appear(DOCK_CHECK, offset=(20, 20)):
                self.handle_dock_cards_loading()
                return
            if click_timer.reached():
                self.device.click(button)
                click_timer.reset()
            if self.handle_popup_confirm('FLEET_SCHEDULER_ENTER'):
                continue
        logger.warning(f'Failed to open dock from slot {button}, skip')

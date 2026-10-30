import json

import numpy as np

from module.base.timer import Timer
from module.base.utils import rgb2gray
from module.config.deep import deep_get
from module.equipment.assets import (
    FLEET_ENTER, FLEET_ENTER_FLAGSHIP, FLEET_1_BACKLINE_1, FLEET_1_BACKLINE_3,
    FLEET_1_VANGUARD_1, FLEET_1_VANGUARD_3, FLEET_2_BACKLINE_1, FLEET_2_BACKLINE_3,
    FLEET_2_VANGUARD_1, FLEET_2_VANGUARD_3, FLEET_NEXT, FLEET_PREV, OCR_FLEET_INDEX)
from module.fleet_scheduler import assets as fleet_assets
from module.logger import logger
from module.map.assets import FLEET_PREPARATION
from module.ocr.ocr import Digit
from module.retire.assets import DOCK_CHECK
from module.retire.dock import Dock
from module.retire.scanner import ShipScanner
from module.ui.page import page_fleet

# Fleet number indicator on the formation screen (same wrapper as
# gems_farming.ui_goto_fleet).
FLEET_INDEX = Digit(OCR_FLEET_INDEX, letter=(90, 154, 255), threshold=128, alphabet='123456')


class FleetSchedulerError(Exception):
    pass


def _slot_button(fleet_index, suffix):
    """
    Args:
        fleet_index (int): 1 or 2.
        suffix (str): One of BACKLINE_1, BACKLINE_2, BACKLINE_3,
            VANGUARD_1, VANGUARD_2, VANGUARD_3.

    Returns:
        Button: Click anchor of a fleet slot on the event preparation
            screen. Slot 2 comes from module/fleet_scheduler/assets.py
            (hand-derived), slot 1 and 3 from module/equipment/assets.py
            (upstream).
    """
    try:
        return getattr(fleet_assets, f'FLEET_{fleet_index}_{suffix}')
    except AttributeError:
        return globals()[f'FLEET_{fleet_index}_{suffix}']


class FleetScheduler(Dock):
    """
    Fill and rotate farm-fleet ships for PR/DR research EXP farming.

    Azdaily fork. Two operating surfaces:

    - Main chapters (folder `campaign_main`): the select-fleet popup does
      not allow member editing, so fleets are arranged on the formation
      screen (page_fleet) before entering the map. Only the flagship and
      the vanguard lead have fixed swap hotspots there, which maps to the
      `Flagship` and `Vanguard1` slot args. Hooked in CampaignRun.run().
    - Event and war-archives stages: member editing is allowed on the
      select-fleet popup, so all six slots per fleet are arranged right
      on the preparation screen. Hooked in FleetPreparation.fleet_preparation().

    Farm fleets are addressed by their fleet number (FarmFleet1.Number /
    FarmFleet2.Number, default 5 and 6); fleets not configured here are
    never touched. Slot values are `keep` (never touched), a dock-filter
    faction (e.g. `iron`), or `research` (follow the shipyard target,
    see module/fleet_scheduler/research.py).
    """

    # Slot arg -> (button suffix on the event preparation screen, dock filter row)
    SLOT_PLAN_ARGS = {
        'Backline1': ('BACKLINE_1', 'main'),
        'Flagship': ('BACKLINE_2', 'main'),
        'Backline3': ('BACKLINE_3', 'main'),
        'Vanguard1': ('VANGUARD_1', 'vanguard'),
        'Vanguard2': ('VANGUARD_2', 'vanguard'),
        'Vanguard3': ('VANGUARD_3', 'vanguard'),
    }

    # Slots editable on the formation screen (main chapters).
    FORMATION_SLOTS = {
        'Flagship': ('main', FLEET_ENTER_FLAGSHIP),
        'Vanguard1': ('vanguard', FLEET_ENTER),
    }

    # Flatness of a slot anchor below this standard deviation is
    # considered an empty slot frame on the event preparation screen
    # (same trick as FleetOperator.in_use, looser bound).
    EMPTY_SLOT_STD = 12

    # ---------- config reading ----------

    @classmethod
    def farm_fleets(cls, config_data):
        """
        Read the configured farm fleets.

        Args:
            config_data (dict): Full config JSON, e.g. self.config.data.

        Returns:
            list[dict]: [{'group': 1, 'number': 5, 'slots': {arg: value}}, ...]
                Groups with Number=0 or no non-keep slot are omitted.
        """
        fleets = []
        for group in (1, 2):
            number = int(deep_get(
                config_data, keys=f'FleetScheduler.FarmFleet{group}.Number', default=0) or 0)
            if number <= 0:
                continue
            slots = {}
            for arg in cls.SLOT_PLAN_ARGS:
                value = deep_get(config_data, keys=f'FleetScheduler.FarmFleet{group}.{arg}', default='keep')
                if value and value != 'keep':
                    slots[arg] = value
            if slots:
                fleets.append({'group': group, 'number': number, 'slots': slots})
        return fleets

    @staticmethod
    def research_state(config_data):
        """
        Args:
            config_data (dict): Full config JSON.

        Returns:
            dict or None: Cached research target
                ({'state': 'farming', 'factions': [...], 'row': ...}),
                None when missing, stale or done.
        """
        raw = deep_get(config_data, keys='FleetScheduler.Rotation.ResearchState', default='')
        try:
            state = json.loads(raw) if raw else None
        except ValueError:
            state = None
        if not isinstance(state, dict) or state.get('state') != 'farming':
            return None
        return state

    @classmethod
    def _has_research_slot(cls, config_data):
        return any(value == 'research' for fleet in cls.farm_fleets(config_data)
                   for value in fleet['slots'].values())

    def _rotation_args(self):
        rotate_level = int(deep_get(self.config.data, keys='FleetScheduler.Rotation.RotateLevel', default=100) or 0)
        level_min = int(deep_get(self.config.data, keys='FleetScheduler.Rotation.LevelMin', default=1) or 1)
        emotion_min = int(deep_get(self.config.data, keys='FleetScheduler.Rotation.EmotionMin', default=0) or 0)
        descending = deep_get(
            self.config.data, keys='FleetScheduler.Rotation.CandidateSort', default='level_asc') == 'level_desc'
        return rotate_level, level_min, emotion_min, descending

    # ---------- entries ----------

    def before_stage(self):
        """
        Called once per campaign task run, from CampaignRun.run(), before
        entering any map. Refreshes the research target cache and, for
        main chapters, arranges farm fleets on the formation screen.
        """
        fleets = self.farm_fleets(self.config.data)
        if not fleets:
            return False
        logger.hr('Fleet scheduler', level=1)
        if self._has_research_slot(self.config.data):
            try:
                from module.fleet_scheduler.research import read_research_state
                read_research_state(self.config, self.device)
            except Exception as e:
                logger.warning(f'FleetScheduler: failed to read research state: {e}')
                self.config.cross_set('FleetScheduler.Rotation.ResearchState', '')
        folder = getattr(self.config, 'Campaign_Event', '')
        if folder == 'campaign_main':
            return self.assign_on_formation(fleets)
        return False

    def apply_on_preparation(self, role_fleets):
        """
        Arrange fleet slots on the event preparation screen.

        Args:
            role_fleets (tuple[int, int]): Fleet numbers the current map
                assigned to its first and second fleet slot
                (config.Fleet_Fleet1 / Fleet_Fleet2). A farm fleet group
                only applies when its Number matches one of them.
        """
        fleets = self.farm_fleets(self.config.data)
        if not fleets:
            return False
        logger.hr('Fleet scheduler', level=1)
        self.handle_info_bar()
        applied = False
        for fleet in fleets:
            group, number = fleet['group'], fleet['number']
            for role, role_fleet in enumerate(role_fleets, start=1):
                if role_fleet != number:
                    continue
                for arg, value in fleet['slots'].items():
                    suffix, row = self.SLOT_PLAN_ARGS[arg]
                    try:
                        factions = self._resolve_factions(value, row)
                    except FleetSchedulerError as e:
                        logger.info(str(e))
                        continue
                    try:
                        if self._assign_preparation_slot(
                                role=role, arg=arg, suffix=suffix, row=row,
                                factions=factions, fleet_number=role_fleet):
                            applied = True
                    except Exception as e:
                        logger.warning(f'FleetScheduler error on fleet {number} {arg}: {e}')
                        if self.appear(DOCK_CHECK, offset=(20, 20)):
                            self.dock_quit()
                        logger.info('Skip remaining slots to keep the sortie going')
                        return applied
        return applied

    def assign_on_formation(self, fleets=None):
        """
        Arrange farm fleets on the formation screen (main chapters).

        Args:
            fleets (list[dict]): See farm_fleets(). Read from config when
                omitted.

        Returns:
            bool: True if any slot has been (re)assigned.
        """
        if fleets is None:
            fleets = self.farm_fleets(self.config.data)
        if not fleets:
            return False
        logger.hr('Fleet scheduler (formation)', level=1)
        applied = False
        for fleet in fleets:
            number = fleet['number']
            if not self._switch_fleet(number):
                continue
            for arg, value in fleet['slots'].items():
                if arg not in self.FORMATION_SLOTS:
                    logger.info(f'Slot {arg} is only editable on event preparation screens, skip')
                    continue
                row, hotspot = self.FORMATION_SLOTS[arg]
                try:
                    factions = self._resolve_factions(value, row)
                except FleetSchedulerError as e:
                    logger.info(str(e))
                    continue
                try:
                    if self._assign_formation_slot(
                            arg=arg, row=row, hotspot=hotspot,
                            factions=factions, fleet_number=number):
                        applied = True
                except Exception as e:
                    logger.warning(f'FleetScheduler error on fleet {number} {arg}: {e}')
                    if self.appear(DOCK_CHECK, offset=(20, 20)):
                        self.dock_quit()
                    break
        return applied

    # ---------- slot resolution ----------

    def _resolve_factions(self, value, row):
        """
        Args:
            value (str): Slot config value: a faction or 'research'.
            row (str): Row of the slot being resolved.

        Returns:
            list[str]: Dock filter factions.

        Raises:
            FleetSchedulerError: When the slot cannot be resolved and
                should be skipped.
        """
        if value != 'research':
            return [value]
        state = self.research_state(self.config.data)
        if state is None:
            raise FleetSchedulerError('Research target is done or unread, skip research slot')
        if state['row'] != row:
            raise FleetSchedulerError(
                f'Research target farms {state["row"]} fleet, slot row is {row}; '
                f'set the matching slot to research instead')
        return state['factions']

    # ---------- formation screen (main chapters) ----------

    def _switch_fleet(self, number):
        """
        Select a fleet on the formation screen.

        Returns:
            bool: True if the fleet is displayed.
        """
        self.ui_ensure(page_fleet)
        retry = Timer(1, count=2)
        for _ in self.loop(timeout=20):
            current = FLEET_INDEX.ocr(self.device.image)
            logger.attr('Fleet index', current)
            if current == 0:
                continue
            diff = number - current
            if diff == 0:
                return True
            if retry.reached():
                button = FLEET_NEXT if diff > 0 else FLEET_PREV
                self.device.multi_click(button, n=min(abs(diff), 6), interval=(0.2, 0.3))
                retry.reset()
        logger.warning(f'FleetScheduler: failed to switch to fleet {number}, skip')
        return False

    def _assign_formation_slot(self, arg, row, hotspot, factions, fleet_number):
        """
        Fill or rotate the flagship / vanguard lead of the displayed
        fleet, from the formation screen.

        Returns:
            bool: True if the slot has been (re)assigned.

        Raises:
            FleetSchedulerError: When the dock cannot be opened.
        """
        rotate_level, level_min, emotion_min, descending = self._rotation_args()
        level_max = rotate_level if rotate_level > 0 else 125

        logger.info(f'FleetScheduler: fleet {fleet_number} {arg}, '
                    f'factions {factions}, row {row}')
        if not self._enter_slot_dock(hotspot, timeout=15):
            raise FleetSchedulerError(f'Cannot open dock from {hotspot}')
        return self._dock_assign(
            arg=arg, row=row, factions=factions, fleet_number=fleet_number,
            rotate_level=rotate_level, level_min=level_min, level_max=level_max,
            emotion_min=emotion_min, descending=descending,
            check_button=page_fleet.check_button, empty=None)

    # ---------- event preparation screen ----------

    def _assign_preparation_slot(self, role, arg, suffix, row, factions, fleet_number):
        """
        Fill or rotate one slot on the event preparation screen.

        Returns:
            bool: True if the slot has been (re)assigned.

        Raises:
            FleetSchedulerError: When the dock cannot be opened.
        """
        rotate_level, level_min, emotion_min, descending = self._rotation_args()
        level_max = rotate_level if rotate_level > 0 else 125

        button = _slot_button(role, suffix)
        empty = self._slot_is_empty(button)
        logger.attr('Slot empty', empty)

        if not self._enter_slot_dock(button, timeout=30):
            raise FleetSchedulerError(f'Cannot open dock from {button}')
        return self._dock_assign(
            arg=arg, row=row, factions=factions, fleet_number=fleet_number,
            rotate_level=rotate_level, level_min=level_min, level_max=level_max,
            emotion_min=emotion_min, descending=descending,
            check_button=FLEET_PREPARATION, empty=empty)

    def _slot_is_empty(self, button):
        """
        Args:
            button (Button): Slot anchor on the event preparation screen.

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

    # ---------- shared dock flow ----------

    def _enter_slot_dock(self, button, timeout):
        """
        Click a fleet slot / swap hotspot until the dock opens.

        Returns:
            bool: True if the dock is open.
        """
        logger.info(f'Enter dock from {button}')
        self.interval_clear(DOCK_CHECK)
        click_timer = Timer(3, count=6)
        for _ in self.loop(timeout=timeout):
            if self.appear(DOCK_CHECK, offset=(20, 20)):
                self.handle_dock_cards_loading()
                return True
            if click_timer.reached():
                self.device.click(button)
                click_timer.reset()
            if self.handle_popup_confirm('FLEET_SCHEDULER_ENTER'):
                continue
        logger.warning(f'Failed to open dock from {button}')
        return False

    def _dock_assign(self, arg, row, factions, fleet_number, rotate_level,
                     level_min, level_max, emotion_min, descending,
                     check_button, empty=None):
        """
        Shared dock flow: filter, scan, decide, select. The dock must be
        open on entry; on return the caller is back on `check_button`.

        Args:
            empty (bool or None): Slot emptiness when known (event
                preparation screen), None when unknown (formation screen,
                assume occupied).

        Rotation rule: with lowest-level-first assignment, the occupant of
        a farm slot is the lowest level ship of its faction and row, so
        the decision reads the MINIMUM level among fleet ships visible in
        the filtered dock. Swap when the slot is empty, the occupant is
        of another faction, or that minimum reaches RotateLevel.
        """
        occupied = True if empty is None else (not empty)
        self.dock_favourite_set(False, wait_loading=False)
        self.dock_sort_method_dsc_set(descending, wait_loading=False)
        self.dock_filter_set(sort='level', index=row, faction=factions, extra='not_level_max')

        # fleet=None: keep fleet badges in the results so ships already in
        # fleets can be told apart from candidates (badge 0).
        scanner = ShipScanner(
            level=(level_min, level_max), emotion=(emotion_min, 150), fleet=None, status='free')
        ships = scanner.scan(self.device.image, output=False)
        candidates = [ship for ship in ships if ship.fleet == 0]
        in_farm = [ship for ship in ships if ship.fleet == fleet_number]

        if occupied and in_farm \
                and (rotate_level <= 0 or min(ship.level for ship in in_farm) < rotate_level):
            logger.info(f'Keep slot {arg}, fleet {fleet_number} {row} ships of '
                        f'{factions}: {[ship.level for ship in in_farm]} '
                        f'all below {rotate_level or "n/a"}')
            self.dock_reset()
            self.dock_quit()
            return False

        if not candidates:
            logger.warning(f'No candidate for {factions}, row {row}, '
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
        self.dock_select_confirm(check_button=check_button)
        logger.info(f'Assign slot {arg} of fleet {fleet_number} success')
        return True

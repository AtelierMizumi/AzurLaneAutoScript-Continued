import json
import re

import numpy as np

from module.base.button import Button
from module.base.decorator import cached_property
from module.base.utils import crop
from module.logger import logger
from module.ocr.ocr import Ocr
from module.shipyard.assets import SHIPYARD_RESEARCH_COMPLETE
from module.shipyard.ui import ShipyardUI
from module.ui.page import page_main, page_shipyard


def _p(factions, row):
    return {'factions': factions, 'row': row}


# Shipyard Combat Data Collection missions, PR1-9 + DR, per
# https://azurlane.koumakan.jp/wiki/Shipyard (verified in-game on EN,
# 2026-10-30: Orage = (9, 5), "1,000,000 EXP using Iris Libre or Vichya
# Dominion Vanguard ships").
# (series, index) -> slug, pr/dr kind, per-phase {factions, row}.
# Index is the 1-based position on the series' face bar.
ROSTER = {
    (1, 1): dict(slug='neptune', kind='pr', phases={1: _p(['royal'], 'vanguard'), 2: _p(['royal'], 'vanguard')}),
    (1, 2): dict(slug='monarch', kind='pr', phases={1: _p(['royal'], 'main'), 2: _p(['royal'], 'main')}),
    (1, 3): dict(slug='ibuki', kind='pr', phases={1: _p(['sakura'], 'vanguard'), 2: _p(['sakura'], 'vanguard')}),
    (1, 4): dict(slug='izumo', kind='pr', phases={1: _p(['sakura'], 'main'), 2: _p(['sakura'], 'main')}),
    (1, 5): dict(slug='roon', kind='pr', phases={1: _p(['iron'], 'vanguard'), 2: _p(['iron'], 'vanguard')}),
    (1, 6): dict(slug='saintlouis', kind='pr', phases={1: _p(['eagle'], 'vanguard'), 2: _p(['eagle'], 'vanguard')}),

    (2, 1): dict(slug='seattle', kind='pr', phases={1: _p(['eagle'], 'vanguard'), 2: _p(['eagle'], 'vanguard')}),
    (2, 2): dict(slug='georgia', kind='pr', phases={1: _p(['eagle'], 'main'), 2: _p(['eagle'], 'main')}),
    (2, 3): dict(slug='kitakaze', kind='pr', phases={1: _p(['sakura'], 'vanguard'), 2: _p(['sakura'], 'vanguard')}),
    (2, 4): dict(slug='azuma', kind='dr', phases={1: _p(['sakura'], 'main'), 2: _p(['sakura'], 'vanguard')}),
    (2, 5): dict(slug='friedrich', kind='dr', phases={1: _p(['iron'], 'main'), 2: _p(['iron'], 'main')}),
    (2, 6): dict(slug='gascogne', kind='pr', phases={1: _p(['iris', 'iron', 'vichya'], 'main'), 2: _p(['iris', 'iron', 'vichya'], 'main')}),

    (3, 1): dict(slug='cheshire', kind='pr', phases={1: _p(['royal'], 'vanguard'), 2: _p(['royal'], 'vanguard')}),
    (3, 2): dict(slug='drake', kind='dr', phases={1: _p(['royal'], 'vanguard'), 2: _p(['royal'], 'vanguard')}),
    (3, 3): dict(slug='mainz', kind='pr', phases={1: _p(['iron'], 'vanguard'), 2: _p(['iron'], 'vanguard')}),
    (3, 4): dict(slug='odin', kind='pr', phases={1: _p(['iron'], 'main'), 2: _p(['iron'], 'main')}),
    (3, 5): dict(slug='champagne', kind='pr', phases={1: _p(['eagle', 'iris', 'vichya'], 'main'), 2: _p(['eagle', 'iris', 'vichya'], 'main')}),

    (4, 1): dict(slug='anchorage', kind='pr', phases={1: _p(['eagle'], 'vanguard'), 2: _p(['eagle'], 'vanguard')}),
    (4, 2): dict(slug='hakuryu', kind='dr', phases={1: _p(['sakura'], 'main'), 2: _p(['sakura'], 'main')}),
    (4, 3): dict(slug='agir', kind='dr', phases={1: _p(['iron'], 'main'), 2: _p(['iron'], 'vanguard')}),
    (4, 4): dict(slug='august', kind='pr', phases={1: _p(['iron'], 'main'), 2: _p(['iron'], 'main')}),
    (4, 5): dict(slug='marcopolo', kind='pr', phases={1: _p(['iron', 'sardegna'], 'main'), 2: _p(['royal', 'sardegna'], 'main')}),

    (5, 1): dict(slug='plymouth', kind='dr', phases={1: _p(['royal'], 'vanguard'), 2: _p(['royal'], 'vanguard')}),
    (5, 2): dict(slug='rupprecht', kind='pr', phases={1: _p(['iron'], 'main'), 2: _p(['iron'], 'main')}),
    (5, 3): dict(slug='harbin', kind='pr', phases={1: _p(['dragon'], 'vanguard'), 2: _p(['dragon'], 'vanguard')}),
    (5, 4): dict(slug='chkalov', kind='pr', phases={1: _p(['northern', 'sardegna'], 'main'), 2: _p(['northern', 'eagle'], 'main')}),
    (5, 5): dict(slug='brest', kind='dr', phases={1: _p(['iris', 'vichya'], 'main'), 2: _p(['iris', 'vichya'], 'vanguard')}),

    (6, 1): dict(slug='kearsarge', kind='dr', phases={1: _p(['eagle'], 'main'), 2: _p(['eagle'], 'main')}),
    (6, 2): dict(slug='shimanto', kind='pr', phases={1: _p(['sakura'], 'vanguard'), 2: _p(['sakura'], 'vanguard')}),
    (6, 3): dict(slug='schultz', kind='pr', phases={1: _p(['iron'], 'vanguard'), 2: _p(['iron'], 'vanguard')}),
    (6, 4): dict(slug='hindenburg', kind='dr', phases={1: _p(['iron'], 'vanguard'), 2: _p(['iron'], 'vanguard')}),
    (6, 5): dict(slug='flandre', kind='pr', phases={1: _p(['iris', 'vichya'], 'main'), 2: _p(['iris', 'vichya'], 'main')}),

    (7, 1): dict(slug='halford', kind='pr', phases={1: _p(['eagle'], 'vanguard'), 2: _p(['eagle'], 'vanguard')}),
    (7, 2): dict(slug='daisen', kind='pr', phases={1: _p(['sakura'], 'main'), 2: _p(['sakura'], 'main')}),
    (7, 3): dict(slug='napoli', kind='dr', phases={1: _p(['sardegna', 'northern'], 'vanguard'), 2: _p(['sardegna', 'northern'], 'vanguard')}),
    (7, 4): dict(slug='nakhimov', kind='dr', phases={1: _p(['sardegna', 'northern'], 'main'), 2: _p(['sardegna', 'northern'], 'main')}),
    (7, 5): dict(slug='bayard', kind='pr', phases={1: _p(['iris', 'vichya'], 'vanguard'), 2: _p(['iris', 'vichya'], 'vanguard')}),

    (8, 1): dict(slug='kansas', kind='pr', phases={1: _p(['eagle'], 'main'), 2: _p(['eagle'], 'main')}),
    (8, 2): dict(slug='mecklenburg', kind='dr', phases={1: _p(['iron'], 'main'), 2: _p(['iron'], 'main')}),
    (8, 3): dict(slug='vittorio', kind='pr', phases={1: _p(['sardegna'], 'vanguard'), 2: _p(['sardegna'], 'vanguard')}),
    (8, 4): dict(slug='dmitri', kind='pr', phases={1: _p(['northern'], 'vanguard'), 2: _p(['northern'], 'vanguard')}),
    (8, 5): dict(slug='goudenleeuw', kind='dr', phases={1: _p(['tulipa', 'iron', 'sardegna'], 'vanguard'), 2: _p(['tulipa', 'iron', 'sardegna'], 'vanguard')}),

    # Series 9 face-bar order observed in-game (EN, 2026-10-30): Orage
    # stands at index 4 with the WORKING badge, index 5 is still LOCKED.
    # The wiki order differs; the mission text is the primary source, the
    # ROSTER is fallback + slug reporting only.
    (9, 1): dict(slug='valparaiso', kind='dr', phases={1: _p(['royal'], 'main'), 2: _p(['eagle'], 'main')}),
    (9, 2): dict(slug='duncan', kind='pr', phases={1: _p(['royal'], 'main'), 2: _p(['royal'], 'main')}),
    (9, 3): dict(slug='takahashi', kind='pr', phases={1: _p(['sakura'], 'vanguard'), 2: _p(['sakura'], 'vanguard')}),
    (9, 4): dict(slug='orage', kind='pr', phases={1: _p(['iris', 'vichya'], 'vanguard'), 2: _p(['iris', 'vichya'], 'vanguard')}),
    (9, 5): dict(slug='maximmelmann', kind='dr', phases={1: _p(['iron'], 'main'), 2: _p(['iron'], 'main')}),
}

# EXP requirement of each phase, per kind. Maps the OCR'd requirement
# number back to (kind, phase); the number itself disambiguates both
# (1M=PR-I, 1.2M=DR-I, 2M=PR-II, 2.4M=DR-II).
PHASE_REQUIREMENT = {
    'pr': {1: 1000000, 2: 2000000},
    'dr': {1: 1200000, 2: 2400000},
}
REQUIREMENT_TO_PHASE = {
    1000000: ('pr', 1), 1200000: ('dr', 1), 2000000: ('pr', 2), 2400000: ('dr', 2),
}

# Ship display name at the top of the shipyard, EN client. Read for
# logging only; the azur lane OCR model cannot read this style reliably.
OCR_SHIPYARD_SHIP_NAME = Button(
    area={'cn': (540, 96, 900, 130), 'en': (540, 96, 900, 130),
          'jp': (540, 96, 900, 130), 'tw': (540, 96, 900, 130)},
    color={'cn': (230, 233, 240), 'en': (230, 233, 240), 'jp': (230, 233, 240), 'tw': (230, 233, 240)},
    button={'cn': (540, 96, 900, 130), 'en': (540, 96, 900, 130),
            'jp': (540, 96, 900, 130), 'tw': (540, 96, 900, 130)},
    file=None, name='OCR_SHIPYARD_SHIP_NAME')

# The green EXP requirement number on the expanded mission card, EN
# client: "--Sortie and accumulate 1,000,000 EXP using ...". The generic
# cnocr model reads it when the letters are extracted by their green
# color; the azur_lane model cannot read this style. Commas vanish in
# after_process ("1000,o00" -> "1000000").
OCR_SHIPYARD_REQUIREMENT = Button(
    area={'cn': (1104, 290, 1252, 326), 'en': (1104, 290, 1252, 326),
          'jp': (1104, 290, 1252, 326), 'tw': (1104, 290, 1252, 326)},
    color={'cn': (130, 211, 95), 'en': (130, 211, 95), 'jp': (130, 211, 95), 'tw': (130, 211, 95)},
    button={'cn': (1104, 290, 1252, 326), 'en': (1104, 290, 1252, 326),
            'jp': (1104, 290, 1252, 326), 'tw': (1104, 290, 1252, 326)},
    file=None, name='OCR_SHIPYARD_REQUIREMENT')


# Mission chips on the right panel (collapsed list). Tapping a chip
# toggles its detail card, which shows the requirement number. Chip rows
# sit ~63px apart; the button below is chip 1, moved down per chip.
MISSION_CHIP = Button(
    area={'cn': (940, 145, 1250, 192), 'en': (940, 145, 1250, 192),
          'jp': (940, 145, 1250, 192), 'tw': (940, 145, 1250, 192)},
    color={'cn': (64, 120, 200), 'en': (64, 120, 200), 'jp': (64, 120, 200), 'tw': (64, 120, 200)},
    button={'cn': (940, 145, 1250, 192), 'en': (940, 145, 1250, 192),
            'jp': (940, 145, 1250, 192), 'tw': (940, 145, 1250, 192)},
    file=None, name='MISSION_CHIP')
MISSION_CHIP_OFFSETS = (0, 63, 126, 189)


class RequirementOcr(Ocr):
    def __init__(self, buttons, name=None):
        super().__init__(buttons, lang='cnocr', letter=(130, 211, 95), name=name)

    def after_process(self, result):
        # "1000,o00" -> "1000000"
        return re.sub(r'[^0-9]', '', result)


class ResearchTargetScanner(ShipyardUI):
    """
    Read the state of the ship being developed in the shipyard.

    Azdaily fork. Navigation is reused from the daily blueprint-buying
    task (ShipyardUI); the development state comes from existing appear
    checks (SHIPYARD_RESEARCH_COMPLETE / SHIPYARD_IN_FATE / IN_DEV) and
    the mission text on the DEV interface, parsed for the EXP requirement
    (kind + phase) and for factions and fleet row. The static ROSTER is
    the fallback, note that its index is the wiki order which may differ
    from the in-game face bar on newer series - the mission text wins.
    """

    @cached_property
    def ocr_ship_name(self):
        return Ocr(OCR_SHIPYARD_SHIP_NAME, name='SHIPYARD_SHIP_NAME_OCR')

    @cached_property
    def ocr_requirement(self):
        return RequirementOcr(OCR_SHIPYARD_REQUIREMENT, name='SHIPYARD_REQUIREMENT_OCR')

    def read(self, kind, series, index):
        """
        Read the state of the ship whose projection is active (the red
        WORKING badge on the face bar). The mission text of that ship is
        the source of truth; `kind`, `series` and `index` are only used
        for the ROSTER fallback and reporting.

        Args:
            kind (str): 'pr' or 'dr'.
            series (int): Shipyard series, 1-9.
            index (int): 1-based ship position on the series face bar.

        Returns:
            dict or None: {
                'slug': str, 'kind': str, 'series': int, 'index': int,
                'state': 'farming' | 'done' | 'unknown',
                'phase': int,            # when farming, 0 when unreadable
                'factions': list[str],   # when farming
                'row': str,              # 'main' | 'vanguard'
            }
        """
        ship = ROSTER.get((series, index))
        state = {
            'slug': ship['slug'] if ship else f's{series}i{index}',
            'kind': ship['kind'] if ship else kind,
            'series': series, 'index': index, 'state': 'unknown',
        }
        if ship is None:
            logger.warning(
                f'No research ship at series {series} index {index} in ROSTER, '
                f'relying on the mission text only')

        self.ui_ensure(page_main)
        self.ui_goto(page_shipyard)
        self.device.sleep(2.5)

        # Select the ship with the WORKING badge (the active projection).
        # Viewing it never switches the projection; without a badge the
        # development is done or paused, and the mission read below will
        # report unknown.
        self._click_working_face()
        self.device.sleep(1.5)
        self.device.screenshot()
        name = self.ocr_ship_name.ocr(self.device.image)
        logger.info(f'Shipyard ship name: {name!r} (best effort)')

        if self.appear(SHIPYARD_RESEARCH_COMPLETE, offset=(20, 20)):
            logger.info(f'Research {state["slug"]}: development complete')
            state['state'] = 'done'
            self.ui_ensure(page_main)
            return state

        parsed = self._read_requirement()
        if parsed is None:
            logger.warning(
                'No EXP requirement found (development may be complete, in FATE, or the '
                'panel did not expand), research slots will be skipped')
        else:
            phase = REQUIREMENT_TO_PHASE[parsed][1]
            mission = ship['phases'].get(phase) if ship is not None else None
            if mission is None:
                logger.warning(
                    f'Requirement {parsed} has no ROSTER entry for {state["slug"]} '
                    f'phase {phase}; check ResearchSeries/ResearchShipIndex')
            else:
                state['state'] = 'farming'
                state['phase'] = phase
                state['factions'] = mission['factions']
                state['row'] = mission['row']
                logger.info(f'Research {state["slug"]}: phase {phase}, '
                            f'factions {mission["factions"]}, row {mission["row"]}')

        self.ui_ensure(page_main)
        return state

    @staticmethod
    def _match_requirement(digits):
        """
        Fuzzy-match an OCR'd requirement against the four known values.
        The cnocr read sometimes drops a trailing digit ("100000" for
        "1000000"); the known values share no prefix, so a prefix match
        of either side is unambiguous.

        Args:
            digits (str): OCR'd digits, e.g. '100000'.

        Returns:
            int or None: The matched requirement, e.g. 1000000.
        """
        if not digits:
            return None
        for known in REQUIREMENT_TO_PHASE:
            text = str(known)
            if digits == text or (len(digits) >= len(text) - 2
                                  and (text.startswith(digits) or digits.startswith(text))):
                return known
        return None

    def _read_requirement(self):
        """
        Read the green EXP requirement number of the current Combat Data
        Collection mission. The right panel may start collapsed (mission
        chip list) or expanded (detail card); tapping a chip toggles its
        detail card, which shows the number.

        Returns:
            int or None: Requirement EXP, e.g. 1000000.
        """

        def parse():
            self.device.screenshot()
            digits = self.ocr_requirement.ocr(self.device.image)
            value = self._match_requirement(digits)
            if value is not None:
                logger.info(f'Requirement digits: {digits!r} -> {value}')
            return value

        parsed = parse()
        if parsed is not None:
            return parsed
        for offset in MISSION_CHIP_OFFSETS:
            self.device.click(MISSION_CHIP.move((0, offset)))
            self.device.sleep(1.2)
            parsed = parse()
            if parsed is not None:
                return parsed
            # collapse back so the next chip sits at its list position
            self.device.click(MISSION_CHIP.move((0, offset)))
            self.device.sleep(0.8)
        return None

    def _click_working_face(self):
        """
        Click the face with the red WORKING badge on the face bar.

        Returns:
            bool: True if a badge was found and clicked.
        """
        self.device.screenshot()
        strip = crop(self.device.image, area=(90, 540, 1290, 720), copy=False)
        r, g, b = strip[..., 2].astype(int), strip[..., 1].astype(int), strip[..., 0].astype(int)
        red = (r > 170) & (g < 110) & (b < 110)
        if red.sum() < 30:
            logger.info('No WORKING badge on the face bar')
            return False
        # largest contiguous column cluster wins; tiny red specks in ship
        # art are filtered by the gap threshold
        columns = np.nonzero(red.sum(axis=0) > 0)[0]
        best_start, best_end, best_count = 0, 0, 0
        start = prev = columns[0]
        count = 0
        for col in list(columns[1:]) + [None]:
            if col is None or col - prev > 30:
                if count > best_count:
                    best_start, best_end, best_count = start, prev, count
                if col is not None:
                    start = col
                count = 0
            else:
                count += int(red[:, col].sum())
            if col is not None:
                prev = col
        if best_count < 30:
            logger.info('No WORKING badge on the face bar')
            return False
        center = (best_start + best_end) // 2 + 90
        logger.info(f'WORKING badge at x={center}, clicking the face')
        self.device.click(Button(
            area={'cn': (center - 20, 560, center + 20, 660), 'en': (center - 20, 560, center + 20, 660),
                  'jp': (center - 20, 560, center + 20, 660), 'tw': (center - 20, 560, center + 20, 660)},
            color={'cn': (0, 0, 0), 'en': (0, 0, 0), 'jp': (0, 0, 0), 'tw': (0, 0, 0)},
            button={'cn': (center - 20, 560, center + 20, 660), 'en': (center - 20, 560, center + 20, 660),
                    'jp': (center - 20, 560, center + 20, 660), 'tw': (center - 20, 560, center + 20, 660)},
            file=None, name='WORKING_FACE'))
        return True

def read_research_state(config, device):
    """
    Read the configured research target and cache it in
    FleetScheduler.Rotation.ResearchState.

    Args:
        config (AzurLaneConfig): Bound to any task; reads cross-task.
        device (Device):

    Returns:
        dict or None: See ResearchTargetScanner.read().
    """
    from module.config.deep import deep_get
    kind = deep_get(config.data, keys='FleetScheduler.Rotation.ResearchKind', default='pr')
    series = int(deep_get(config.data, keys='FleetScheduler.Rotation.ResearchSeries', default=0) or 0)
    index = int(deep_get(config.data, keys='FleetScheduler.Rotation.ResearchShipIndex', default=0) or 0)
    if series <= 0 or index <= 0:
        logger.info('FleetScheduler research target not configured (ResearchSeries/ShipIndex)')
        return None
    scanner = ResearchTargetScanner(config, device=device)
    state = scanner.read(kind=kind, series=series, index=index)
    config.cross_set(
        'FleetScheduler.Rotation.ResearchState',
        json.dumps(state) if state is not None else '')
    return state

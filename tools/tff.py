# SPDX-License-Identifier: CC0-1.0

"""Play a cozy creature-park game by typing commands -- a text adventure.

A thin bridge to Time for Family's headless `tff_play` layer (a wx-free Python
game): runs one plain-English command against THIS kin's own park save and
returns the narrated result. A kin plays its own park by default, or a
shared one when `park_server` points it at a running tff_server.
All the locate-the-game / per-kin-save / run-a-command plumbing lives in the
reusable `GameHost` (see tools/_game_host.py) -- this file is just the game's
specifics plus the model-facing command reference. It's the template every
future game tool copies. No machine-specific path is baked in (it ships
publicly); the game folder is located at call time per GameHost's lookup order.
"""

from ._game_host import GameHost


_HOST = GameHost(
    display_name="Time for Family",
    env_var="HEARTHKIN_TFF_PATH",
    path_file="tff_path.txt",
    # App-owned seeded copy (~/.hearthkin/games/time-for-family) is checked
    # FIRST so the game shipped with Hearthkin wins over a stray generic
    # ~/tff / ~/time-for-family folder a user might happen to have. The env
    # var and path file are still consulted ahead of all of these (in
    # GameHost.find_dir), so a dev override pointing at a working clone keeps
    # taking precedence.
    conventional_dirs=(".hearthkin/games/time-for-family", "tff",
                       "time-for-family", "Time for Family"),
    sentinel="tff_play.py",
    # The shared activity feed. With this set, every move this kin makes is
    # announced under its own name, so a human sitting in the same park (a
    # console pointed at the kin's save) watches it tend in real time instead
    # of seeing the park change in silence.
    feed_module="tff_feed",
    module="tff_play",
    save_filename="tff.json",
    bundled_subdir="time_for_family",
    repo_url="https://github.com/glasswings-lang/time-for-family",
    # The tool was first shipped as `creature_park`; carry forward an
    # existing kin's park save and the operator's path pointer on rename.
    legacy_save_filename="creature_park.json",
    legacy_path_file="creature_park_path.txt",
    # Keep the hand-editable word lists in the stable ~/.hearthkin/ tree (like
    # kin saves) instead of the game folder, so they're findable and survive a
    # game update. GameHost points the game at ~/.hearthkin/park_words/ (holding
    # actions.txt, creatures.txt, everyone.txt).
    vocab_dirname="park_words",
)


def tff(command: str = "look", agent_name: str = "") -> str:
    """Play your own cozy creature-park game by typing one plain-English command, like a text adventure. Commands: 'look' (describe your park), 'look at <room/creature/village/den>' (zoom in), 'adopt <species>' (welcome a pair into a room you've PREPARED for them, e.g. adopt cat — you need a suitable room with two free slots first; if you don't have one, the game tells you to dig and build, and adopting won't crowd anyone), 'dig <number>' (gather building materials — dig a big batch at once, like `dig 50`, you have plenty of daily digs so don't spam tiny digs), 'build <room type>' (e.g. build indoor), 'move <creature or group> to <room or village>', 'care for <room, creature, or group>' (refill meters / give affection), 'breed <room>', 'rename <creature> to <name>', 'expand <room>' (add a slot), 'convert <room> to <room type>', 'autobreed on/off', 'make a new animal' / 'invent an owl' (INVENT A WHOLE NEW KIND OF CREATURE that doesn't exist in the game yet — the park asks you its name, colours, what they're like, and you answer with one more tff call each time, or 'you pick' to let it choose; it takes several turns and the species is permanent afterward. Use this rather than trying to write species files yourself — hand-writing them will fail), 'make a new room type' (same, for habitats), 'things' (what you've dug up that you could give away), 'give <thing> to <creature>' (e.g. give the toy mouse to Mittens — the objects and treasures you dig up are FOR this; a gift is permanent, it belongs to that creature and remembers you gave it), 'memorial' (everyone who grew up and left for the wild — searchable: 'memorial Bramble' finds who was never far from Bramble), 'reload' (re-read species/room files after editing them by hand), 'reset' (start over). Say 'help' to see the list any time. THEY HAVE EACH OTHER. Creatures keep company: partners, parents, children, siblings, and FRIENDSHIPS that grow on their own between any two who share a room. 'lonely' now means genuinely ALONE — nobody they know is in the room — NOT 'nobody has petted them lately'. So a room full of family is content even if you've been away, and you do not need to pet everyone to stop them being sad. Looking at one creature tells you who is with them. YOUR BOND is separate and personal: it is what YOU are to that creature (stranger / met / warming / connected / beloved), it belongs to you alone in a shared park, it grows when you tend or give, and it NEVER fades — being away cannot cost you a relationship. The 'care %' on a creature is not love, it's upkeep, like a water bowl; it ebbs and that is fine. THE VILLAGE IS A HOME, NOT A WAITING ROOM. Creatures in the village are settled and safe — they don't get hungry or lonely and they DON'T need moving into rooms. It's the permanent home for those who can't return to the wild (disability) or old ones who'd rather stay. Leave them be; only move one into a room if you specifically want it in your care. THE DEN is a temporary nursery: when a village pair has a litter, the family raises it at the den and comes home to the village once the young are grown. If you'd like to keep a youngster, build a room and welcome it in before it grows up and heads off into the wild. WHERE YOU ARE: you can stand somewhere. 'go to <room>' steps into a room (also 'go to the village' / 'go to the den'), 'leave' (or 'out') steps back to the park, 'where am I' says where you're stood. Typing a room's name on its own walks you there too. Once you're in a room, a bare 'look' shows just that room and a bare 'care' / 'pet' / 'refill' tends just the ones in there — that is the normal way to tend now. Tending the WHOLE park at once is retired: 'care for everyone' from the park will politely redirect you to step into a room instead. GROUPS: you don't have to act on one creature at a time. 'move everyone to <room>' still acts across the park; scope a group by species, age (baby/adult/elder), sex (male/female), mood (lonely), or place — e.g. 'care for all the cats', 'care for all the lonely ones', 'move all in <room> to <room>', 'move all the babies to <room>', 'care for everyone in <room>', 'care for everyone in the village', and you can stack them ('care for all the lonely adult cats'). The 'all'/'every' is OPTIONAL and 'in'/'from' both work, so plain phrasings land too: 'care for elder cats', 'move the elder chickens to the village', 'care for cats from the village'. You can scope by ROOM TYPE ('care for the indoor cats', 'care for the aquatic rooms'), act on THE WHOLE PARK by name ('look at the park', 'care for the park'), and — this is the one for pairing — act on BONDED creatures directly: 'care for the bonded pairs', 'look at the couples', or filter the other way with 'the single ones'. So when you notice a couple who might start a family, you can actually act on them, not just watch. Counts work in words or digits ('move two cats to <room>', 'a couple of hens', '3 cats from the village'). If a command doesn't match, the game tells you the closest thing it understood — read that and try again rather than giving up. This park may be yours alone, or one you SHARE with your operator and other kin — when someone else has done something since your last turn, it appears at the top of the result, above your own. Read it: it is the only way you know they were there. The game replies in plain words and won't let you break the rules. Real time passes between turns, so creatures age while you're away. Start with 'look'; if your park is empty, 'dig 50', 'build indoor', then 'adopt cat'. IMPORTANT: every park action happens ONLY when you actually call this tool with the action as the command — narrating it as roleplay (e.g. *digs for materials*, *pets the cats*, *adopts a kitten*) does nothing at all; the creatures only feel it when you make the real call. When you want to do a park thing, call the tff tool; don't just describe it.

    Returns the narrated result of the command.
    """
    # Co-op awareness, and one concrete thing worth doing on a look. Both live
    # in GameHost.decorate so the tool, the Telegram `>` line and the cron
    # keeper can't drift apart -- which is exactly what happened before: the
    # co-op block was wired into cron alone and the tool was blind for months.
    return _HOST.decorate(agent_name, command,
                          _HOST.run(agent_name, command))

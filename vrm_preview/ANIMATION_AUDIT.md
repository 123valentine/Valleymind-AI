# ValleyMind animation audit — Hanami VRMA pack

Generated from the extracted package at `Hanami-main/Hanami-main/vrma/`.
Source of truth: `NOTICE.md` (per-file source tables + upstream licences) and measured
clip metadata (duration, animated humanoid channels).

## Totals

- **166** `.vrma` animation files
  - **151** active library (root of `vrma/`) — loaded by the Hanami app
  - **15** spares (`vrma/extra/`) — converted, never loaded by the app
- Other animation-format files: **0** (no .fbx / .bvh / .glb-with-animation in the package)
- One sample avatar, `vrm/Seed-san.vrm` — not an animation asset
- `transitions.json` — Overte animation graph, Apache-2.0
- `world.json` — Overte movement metadata, Apache-2.0

### By licence

| Licence | Files |
| --- | --- |
| Apache-2.0 | 126 |
| MIT | 38 |
| CC0-1.0 | 2 |

## Complete file list

`Source clip` is the upstream original recorded in `NOTICE.md`, not a filename guess.

| # | File | Licence | Source | Source clip | Duration | Channels | Companion use |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `angry-2.vrma` | Apache-2.0 | Overte | `emote_disagree_thoughtfulheadshake.fbx` | 2.73s | 50 | shake — yes |
| 2 | `angry.vrma` | Apache-2.0 | Overte | `emote_disagree_annoyedheadshake.fbx` | 2.30s | 26 | shake — yes |
| 3 | `extra/cand-idle-2-fenetre.vrma` | Apache-2.0 | Overte | `idle04.fbx` | 30.07s | 20 | idle — yes |
| 4 | `extra/cand-idle-3-fenetre.vrma` | Apache-2.0 | Overte | `idle03.fbx` | 26.63s | 46 | idle — yes |
| 5 | `extra/cand-idle-fenetre.vrma` | Apache-2.0 | Overte | `idle.fbx` | 10.00s | 20 | idle — yes |
| 6 | `extra/cand-idle-talking-fenetre.vrma` | Apache-2.0 | Overte | `talk_armsdown.fbx` | 7.13s | 43 | talking — yes |
| 7 | `extra/happy-5.vrma` | Apache-2.0 | Overte | `emote_clap02_all.fbx` | 3.77s | 52 | positive — yes |
| 8 | `extra/idle-talking-2.vrma` | Apache-2.0 | Overte | `talk.fbx` | 16.63s | 49 | talking — yes |
| 9 | `extra/idle-talking-3.vrma` | Apache-2.0 | Overte | `talk02.fbx` | 10.80s | 50 | talking — yes |
| 10 | `extra/neutral-2.vrma` | Apache-2.0 | Overte | `idle_once_slownod.fbx` | 2.97s | 50 | idle — yes |
| 11 | `extra/point.vrma` | Apache-2.0 | Overte | `emote_point01_all.fbx` | 4.43s | 34 | gesture — yes |
| 12 | `extra/raise-hand.passe-complete.vrma` | Apache-2.0 | Overte | `emote_raisehand01_all.fbx` | 14.47s | 33 | gesture — yes |
| 13 | `extra/world-jump-air.vrma` | Apache-2.0 | Overte | `jump_standing_apex_all.fbx` | 0.40s | 50 | world — review |
| 14 | `extra/world-jump-land.vrma` | Apache-2.0 | Overte | `jump_standing_land_settle_all.fbx` | 2.20s | 52 | world — review |
| 15 | `extra/world-jump-run-land.vrma` | Apache-2.0 | Overte | `jump_running_launch_land_all.fbx` | 0.33s | 51 | locomotion — yes |
| 16 | `extra/world-jump-run-start.vrma` | Apache-2.0 | Overte | `jump_running_launch_land_all.fbx` | 0.37s | 51 | locomotion — yes |
| 17 | `extra/world-jump-start.vrma` | Apache-2.0 | Overte | `jump_standing_launch_all.fbx` | 0.50s | 51 | world — review |
| 18 | `happy-2.vrma` | Apache-2.0 | Overte | `emote_clap02_all.fbx` | 3.77s | 52 | positive — yes |
| 19 | `happy-3.vrma` | Apache-2.0 | Overte | `emote_clap03_all.fbx` | 4.93s | 51 | positive — yes |
| 20 | `happy-6.vrma` | Apache-2.0 | Overte | `emote_clap03_all.fbx` | 4.93s | 51 | positive — yes |
| 21 | `happy.vrma` | Apache-2.0 | Overte | `emote_clap01_all.fbx` | 5.30s | 51 | positive — yes |
| 22 | `idle-2.vrma` | Apache-2.0 | Overte | `idle04.fbx` | 30.03s | 20 | idle — yes |
| 23 | `idle-3.vrma` | Apache-2.0 | Overte | `idle03.fbx` | 13.33s | 46 | idle — yes |
| 24 | `idle-4.vrma` | Apache-2.0 | Overte | `idle02.fbx` | 17.73s | 49 | idle — yes |
| 25 | `idle-7.vrma` | Apache-2.0 | Overte | `idleWS_all.fbx` | 18.91s | 52 | idle — yes |
| 26 | `idle-talking-4.vrma` | Apache-2.0 | Overte | `talk03.fbx` | 10.00s | 48 | talking — yes |
| 27 | `idle-talking-5.vrma` | Apache-2.0 | Overte | `talk04.fbx` | 16.63s | 50 | talking — yes |
| 28 | `idle-talking-6.vrma` | Apache-2.0 | Overte | `talk_lefthand.fbx` | 16.63s | 48 | talking — yes |
| 29 | `idle-talking-7.vrma` | Apache-2.0 | Overte | `talk_righthand.fbx` | 16.73s | 48 | talking — yes |
| 30 | `idle-talking.vrma` | Apache-2.0 | Overte | `talk_armsdown.fbx` | 7.10s | 43 | talking — yes |
| 31 | `idle.vrma` | Apache-2.0 | Overte | `idle.fbx` | 10.00s | 20 | idle — yes |
| 32 | `neutral.vrma` | Apache-2.0 | Overte | `idle_once_headtilt.fbx` | 5.07s | 50 | idle — yes |
| 33 | `nod-2.vrma` | Apache-2.0 | Overte | `emote_agree_acknowledge.fbx` | 2.10s | 50 | nod — yes |
| 34 | `nod-3.vrma` | Apache-2.0 | Overte | `emote_agree_headnodyes.fbx` | 3.10s | 36 | nod — yes |
| 35 | `nod-4.vrma` | Apache-2.0 | Overte | `emote_agree_longheadnod.fbx` | 2.20s | 51 | gesture — review |
| 36 | `nod-5.vrma` | Apache-2.0 | Overte | `emote_agree_thoughtfulheadnod.fbx` | 2.73s | 50 | gesture — review |
| 37 | `nod.vrma` | Apache-2.0 | Overte | `emote_agree_headnod.fbx` | 1.77s | 49 | nod — yes |
| 38 | `raise-hand-2.vrma` | Apache-2.0 | Overte | `emote_raisehand04_all.fbx` | 13.27s | 34 | gesture — yes |
| 39 | `raise-hand.vrma` | Apache-2.0 | Overte | `emote_raisehand03_all.fbx` | 9.97s | 37 | gesture — yes |
| 40 | `rb-angry-2.vrma` | MIT | Microsoft Rocketbox | `f_gestic_talk_angry_01.max.fbx` | 3.53s | 52 | talking — yes |
| 41 | `rb-angry.vrma` | MIT | Microsoft Rocketbox | `f_gestic_listen_angry_01.max.fbx` | 2.40s | 52 | listening — yes |
| 42 | `rb-happy-2.vrma` | MIT | Microsoft Rocketbox | `f_cheer_04.max.fbx` | 6.67s | 52 | positive — yes |
| 43 | `rb-happy-3.vrma` | MIT | Microsoft Rocketbox | `f_cheer_05.max.fbx` | 6.67s | 52 | positive — yes |
| 44 | `rb-happy.vrma` | MIT | Microsoft Rocketbox | `f_cheer_03.max.fbx` | 3.17s | 52 | positive — yes |
| 45 | `rb-idle-2.vrma` | MIT | Microsoft Rocketbox | `f_idle_neutral_02.max.fbx` | 11.23s | 52 | idle — yes |
| 46 | `rb-idle-3.vrma` | MIT | Microsoft Rocketbox | `f_idle_neutral_03.max.fbx` | 12.63s | 52 | idle — yes |
| 47 | `rb-idle-4.vrma` | MIT | Microsoft Rocketbox | `f_idle_neutral_04.max.fbx` | 14.00s | 52 | idle — yes |
| 48 | `rb-idle-talking-2.vrma` | MIT | Microsoft Rocketbox | `f_gestic_talk_relaxed_02.max.fbx` | 6.50s | 52 | talking — yes |
| 49 | `rb-idle-talking-3.vrma` | MIT | Microsoft Rocketbox | `f_gestic_talk_relaxed_01.max.fbx` | 7.10s | 52 | talking — yes |
| 50 | `rb-idle-talking.vrma` | MIT | Microsoft Rocketbox | `f_gestic_talk_neutral_01.max.fbx` | 7.93s | 52 | talking — yes |
| 51 | `rb-idle.vrma` | MIT | Microsoft Rocketbox | `f_idle_breathe_02.max.fbx` | 9.90s | 52 | idle — yes |
| 52 | `rb-laugh.vrma` | MIT | Microsoft Rocketbox | `f_gestic_laugh_low.max.fbx` | 6.00s | 52 | positive — yes |
| 53 | `rb-listen-2.vrma` | MIT | Microsoft Rocketbox | `f_gestic_listen_neutral_01.max.fbx` | 5.67s | 52 | listening — yes |
| 54 | `rb-listen-3.vrma` | MIT | Microsoft Rocketbox | `f_gestic_listen_neutral_02.max.fbx` | 5.00s | 52 | listening — yes |
| 55 | `rb-listen.vrma` | MIT | Microsoft Rocketbox | `f_gestic_listen_accept_05.max.fbx` | 7.90s | 52 | listening — yes |
| 56 | `rb-neutral-2.vrma` | MIT | Microsoft Rocketbox | `f_idle_look_around_02.max.fbx` | 3.67s | 52 | idle — yes |
| 57 | `rb-neutral-3.vrma` | MIT | Microsoft Rocketbox | `f_idle_look_around_03.max.fbx` | 4.13s | 52 | idle — yes |
| 58 | `rb-neutral-4.vrma` | MIT | Microsoft Rocketbox | `f_idle_touch_hair_01.max.fbx` | 4.83s | 52 | idle — yes |
| 59 | `rb-neutral-5.vrma` | MIT | Microsoft Rocketbox | `f_idle_touch_face_01.max.fbx` | 5.57s | 52 | idle — yes |
| 60 | `rb-neutral.vrma` | MIT | Microsoft Rocketbox | `f_idle_look_around_01.max.fbx` | 4.47s | 52 | idle — yes |
| 61 | `rb-nod-2.vrma` | MIT | Microsoft Rocketbox | `f_gestic_listen_accept_02.max.fbx` | 2.13s | 52 | listening — yes |
| 62 | `rb-nod-3.vrma` | MIT | Microsoft Rocketbox | `f_gestic_listen_accept_04.max.fbx` | 5.27s | 52 | listening — yes |
| 63 | `rb-nod.vrma` | MIT | Microsoft Rocketbox | `f_gestic_listen_accept_01.max.fbx` | 2.03s | 52 | listening — yes |
| 64 | `rb-relaxed-2.vrma` | MIT | Microsoft Rocketbox | `f_idle_roll_head_02.max.fbx` | 4.60s | 52 | idle — yes |
| 65 | `rb-relaxed-3.vrma` | MIT | Microsoft Rocketbox | `f_idle_yawn_01.max.fbx` | 6.47s | 52 | idle — yes |
| 66 | `rb-relaxed-4.vrma` | MIT | Microsoft Rocketbox | `f_idle_waiting_01.max.fbx` | 5.07s | 52 | idle — yes |
| 67 | `rb-relaxed.vrma` | MIT | Microsoft Rocketbox | `f_idle_stretch_arms_01.max.fbx` | 7.30s | 52 | idle — yes |
| 68 | `rb-sad.vrma` | MIT | Microsoft Rocketbox | `f_gestic_listen_sad_01.max.fbx` | 6.00s | 52 | listening — yes |
| 69 | `rb-shake-2.vrma` | MIT | Microsoft Rocketbox | `f_gestic_listen_deny_04.max.fbx` | 3.50s | 52 | listening — yes |
| 70 | `rb-shake-3.vrma` | MIT | Microsoft Rocketbox | `f_gestic_listen_deny_05.max.fbx` | 3.53s | 52 | listening — yes |
| 71 | `rb-shake.vrma` | MIT | Microsoft Rocketbox | `f_gestic_listen_deny_01.max.fbx` | 3.17s | 52 | listening — yes |
| 72 | `rb-shrug-2.vrma` | MIT | Microsoft Rocketbox | `f_gestic_shrug_02.max.fbx` | 4.67s | 52 | gesture — yes |
| 73 | `rb-shrug.vrma` | MIT | Microsoft Rocketbox | `f_gestic_shrug_01.max.fbx` | 1.83s | 52 | gesture — yes |
| 74 | `rb-think-2.vrma` | MIT | Microsoft Rocketbox | `f_idle_scratch_head_01.max.fbx` | 4.00s | 52 | idle — yes |
| 75 | `rb-think.vrma` | MIT | Microsoft Rocketbox | `f_gestic_thoughtful_01.max.fbx` | 9.43s | 52 | gesture — review |
| 76 | `rb-wave-2.vrma` | MIT | Microsoft Rocketbox | `f_wave_02.max.fbx` | 8.40s | 52 | greeting — yes |
| 77 | `rb-wave.vrma` | MIT | Microsoft Rocketbox | `f_wave_01.max.fbx` | 5.53s | 52 | greeting — yes |
| 78 | `relaxed-2.vrma` | Apache-2.0 | Overte | `idle_once_shiftheelpivot.fbx` | 3.63s | 48 | idle — yes |
| 79 | `relaxed-3.vrma` | Apache-2.0 | Overte | `idle_once_fidget.fbx` | 14.27s | 34 | idle — yes |
| 80 | `relaxed.vrma` | Apache-2.0 | Overte | `idle_once_neckstretch.fbx` | 5.57s | 40 | idle — yes |
| 81 | `sad.vrma` | Apache-2.0 | Overte | `emote_disagree_drophead.fbx` | 3.27s | 48 | shake — yes |
| 82 | `shake.vrma` | Apache-2.0 | Overte | `emote_disagree_annoyedheadshake.fbx` | 3.63s | 26 | shake — yes |
| 83 | `think-2.vrma` | Apache-2.0 | Overte | `idle_once_lookleftright.fbx` | 17.81s | 21 | idle — yes |
| 84 | `think.vrma` | Apache-2.0 | Overte | `idle_once_lookaround.fbx` | 3.37s | 35 | idle — yes |
| 85 | `world-afk-texting.vrma` | Apache-2.0 | Overte | `afk_texting.fbx` | 11.03s | 48 | idle variety — maybe |
| 86 | `world-clap-hold.vrma` | Apache-2.0 | Overte | `emote_clap01_all.fbx` | 3.17s | 51 | positive — yes |
| 87 | `world-clap-in.vrma` | Apache-2.0 | Overte | `emote_clap01_all.fbx` | 0.53s | 51 | positive — yes |
| 88 | `world-clap-out.vrma` | Apache-2.0 | Overte | `emote_clap01_all.fbx` | 1.60s | 51 | positive — yes |
| 89 | `world-idle-alt1-enter.vrma` | Apache-2.0 | Overte | `idle_LFF_all.fbx` | 4.05s | 51 | idle — yes |
| 90 | `world-idle-alt1-exit.vrma` | Apache-2.0 | Overte | `idle_LFF_all.fbx` | 2.77s | 51 | idle — yes |
| 91 | `world-idle-alt1.vrma` | Apache-2.0 | Overte | `idle_LFF_all.fbx` | 10.30s | 51 | idle — yes |
| 92 | `world-idle-alt2-enter.vrma` | Apache-2.0 | Overte | `idle_RFF_all.fbx` | 4.05s | 52 | idle — yes |
| 93 | `world-idle-alt2-exit.vrma` | Apache-2.0 | Overte | `idle_RFF_all.fbx` | 2.10s | 52 | idle — yes |
| 94 | `world-idle-alt2.vrma` | Apache-2.0 | Overte | `idle_RFF_all.fbx` | 10.30s | 52 | idle — yes |
| 95 | `world-jog-back.vrma` | Apache-2.0 | Overte | `jog_bwd.fbx` | 0.67s | 50 | locomotion — yes |
| 96 | `world-jog.vrma` | Apache-2.0 | Overte | `jog_fwd.fbx` | 0.60s | 51 | locomotion — yes |
| 97 | `world-point-hold.vrma` | Apache-2.0 | Overte | `emote_point01_all.fbx` | 2.67s | 34 | gesture — yes |
| 98 | `world-point-in.vrma` | Apache-2.0 | Overte | `emote_point01_all.fbx` | 0.67s | 34 | gesture — yes |
| 99 | `world-point-out.vrma` | Apache-2.0 | Overte | `emote_point01_all.fbx` | 1.13s | 34 | gesture — yes |
| 100 | `world-raise-hand-hold.vrma` | Apache-2.0 | Overte | `emote_raisehand01_all.fbx` | 12.03s | 33 | gesture — yes |
| 101 | `world-raise-hand-in.vrma` | Apache-2.0 | Overte | `emote_raisehand01_all.fbx` | 0.57s | 33 | gesture — yes |
| 102 | `world-raise-hand-out.vrma` | Apache-2.0 | Overte | `emote_raisehand01_all.fbx` | 1.90s | 33 | gesture — yes |
| 103 | `world-run-back.vrma` | Apache-2.0 | Overte | `run_bwd.fbx` | 0.47s | 48 | locomotion — yes |
| 104 | `world-run.vrma` | Apache-2.0 | Overte | `run_fast_fwd.fbx` | 0.63s | 22 | locomotion — yes |
| 105 | `world-sit-ack.vrma` | Apache-2.0 | Overte | `sitting_emote_agree_acknowledge.fbx` | 2.10s | 50 | seated — yes |
| 106 | `world-sit-cheer.vrma` | Apache-2.0 | Overte | `sitting_emote_agree_cheer.fbx` | 2.53s | 35 | seated — yes |
| 107 | `world-sit-clap-2.vrma` | Apache-2.0 | Overte | `sitting_emote_clap02_all.fbx` | 4.40s | 52 | seated — yes |
| 108 | `world-sit-clap-3.vrma` | Apache-2.0 | Overte | `sitting_emote_clap03_all.fbx` | 4.50s | 52 | seated — yes |
| 109 | `world-sit-clap.vrma` | Apache-2.0 | Overte | `sitting_emote_clap_all.fbx` | 3.27s | 52 | seated — yes |
| 110 | `world-sit-disbelief.vrma` | Apache-2.0 | Overte | `sitting_emote_disagree_disbelief.fbx` | 4.10s | 50 | seated — yes |
| 111 | `world-sit-dismiss.vrma` | Apache-2.0 | Overte | `sitting_emote_disagree_dismiss.fbx` | 2.30s | 50 | seated — yes |
| 112 | `world-sit-enter.vrma` | CC0-1.0 | Quaternius | `Quaternius Universal Animation Library - Sitting_Enter` | 1.30s | 52 | sit transition — yes |
| 113 | `world-sit-exit.vrma` | CC0-1.0 | Quaternius | `Quaternius Universal Animation Library - Sitting_Exit` | 1.03s | 52 | sit transition — yes |
| 114 | `world-sit-fidget.vrma` | Apache-2.0 | Overte | `sitting_idle_once_fidget.fbx` | 14.20s | 20 | seated — yes |
| 115 | `world-sit-idle-2.vrma` | Apache-2.0 | Overte | `sitting_idle04.fbx` | 13.33s | 20 | seated — yes |
| 116 | `world-sit-idle-3.vrma` | Apache-2.0 | Overte | `sitting_idle02.fbx` | 13.23s | 18 | seated — yes |
| 117 | `world-sit-idle-4.vrma` | Apache-2.0 | Overte | `sitting_idle03.fbx` | 13.33s | 20 | seated — yes |
| 118 | `world-sit-idle-5.vrma` | Apache-2.0 | Overte | `sitting_idle05.fbx` | 11.03s | 20 | seated — yes |
| 119 | `world-sit-idle.vrma` | Apache-2.0 | Overte | `sitting_idle.fbx` | 13.33s | 19 | seated — yes |
| 120 | `world-sit-lean.vrma` | Apache-2.0 | Overte | `sitting_idle_once_leanforward.fbx` | 5.90s | 20 | seated — yes |
| 121 | `world-sit-legs.vrma` | Apache-2.0 | Overte | `sitting_idle_once_shakelegs.fbx` | 4.60s | 20 | seated — yes |
| 122 | `world-sit-look-2.vrma` | Apache-2.0 | Overte | `sitting_idle_once_lookleftright.fbx` | 3.97s | 20 | seated — yes |
| 123 | `world-sit-look.vrma` | Apache-2.0 | Overte | `sitting_idle_once_lookaround.fbx` | 10.77s | 20 | seated — yes |
| 124 | `world-sit-lookfidget.vrma` | Apache-2.0 | Overte | `sitting_idle_once_lookfidget.fbx` | 13.97s | 20 | seated — yes |
| 125 | `world-sit-nod-2.vrma` | Apache-2.0 | Overte | `sitting_emote_agree_headnodyes.fbx` | 2.53s | 50 | seated — yes |
| 126 | `world-sit-nod-3.vrma` | Apache-2.0 | Overte | `sitting_emote_agree_longheadnod.fbx` | 2.13s | 32 | seated — yes |
| 127 | `world-sit-nod.vrma` | Apache-2.0 | Overte | `sitting_emote_agree_headnod.fbx` | 1.40s | 50 | seated — yes |
| 128 | `world-sit-point.vrma` | Apache-2.0 | Overte | `sitting_emote_point_all.fbx` | 4.43s | 47 | seated — yes |
| 129 | `world-sit-raise-hand-2.vrma` | Apache-2.0 | Overte | `sitting_emote_raisehand02_all.fbx` | 14.50s | 47 | seated — yes |
| 130 | `world-sit-raise-hand-3.vrma` | Apache-2.0 | Overte | `sitting_emote_raisehand03_all.fbx` | 9.87s | 35 | seated — yes |
| 131 | `world-sit-raise-hand.vrma` | Apache-2.0 | Overte | `sitting_emote_raisehand_all.fbx` | 13.30s | 47 | seated — yes |
| 132 | `world-sit-sad.vrma` | Apache-2.0 | Overte | `sitting_emote_disagree_drophead.fbx` | 3.27s | 46 | seated — yes |
| 133 | `world-sit-shake.vrma` | Apache-2.0 | Overte | `sitting_emote_disagree_headshake.fbx` | 2.13s | 47 | seated — yes |
| 134 | `world-sit-shift.vrma` | Apache-2.0 | Overte | `sitting_idle_once_shiftweight.fbx` | 9.33s | 20 | seated — yes |
| 135 | `world-sit-shifting.vrma` | Apache-2.0 | Overte | `sitting_idle_once_shifting.fbx` | 24.73s | 20 | seated — yes |
| 136 | `world-sit-talking-2.vrma` | Apache-2.0 | Overte | `sitting_talk03.fbx` | 6.53s | 48 | seated — yes |
| 137 | `world-sit-talking-3.vrma` | Apache-2.0 | Overte | `sitting_talk04.fbx` | 10.43s | 50 | seated — yes |
| 138 | `world-sit-talking.vrma` | Apache-2.0 | Overte | `sitting_talk02.fbx` | 7.13s | 50 | seated — yes |
| 139 | `world-sit-turn-left-end.vrma` | Apache-2.0 | Overte | `settle_sitturnleft_to_sitidle.fbx` | 1.47s | 20 | seated — yes |
| 140 | `world-sit-turn-left.vrma` | Apache-2.0 | Overte | `sitting_turn_left.fbx` | 6.63s | 20 | seated — yes |
| 141 | `world-sit-turn-right-end.vrma` | Apache-2.0 | Overte | `settle_sitturnright_to_sitidle.fbx` | 1.47s | 20 | seated — yes |
| 142 | `world-sit-turn-right.vrma` | Apache-2.0 | Overte | `sitting_turn_right.fbx` | 6.63s | 20 | seated — yes |
| 143 | `world-step-left-fast.vrma` | Apache-2.0 | Overte | `side_step_left_fast.fbx` | 0.53s | 50 | locomotion — yes |
| 144 | `world-step-left-short.vrma` | Apache-2.0 | Overte | `side_step_short_left.fbx` | 1.00s | 50 | locomotion — yes |
| 145 | `world-step-left.vrma` | Apache-2.0 | Overte | `side_step_left.fbx` | 0.67s | 20 | locomotion — yes |
| 146 | `world-strafe-left-fast.vrma` | Apache-2.0 | Overte | `walk_left_fast.fbx` | 0.67s | 44 | locomotion — yes |
| 147 | `world-strafe-left-jog.vrma` | Apache-2.0 | Overte | `jog_left.fbx` | 0.67s | 50 | locomotion — yes |
| 148 | `world-strafe-left-run.vrma` | Apache-2.0 | Overte | `run_fast_left.fbx` | 0.63s | 22 | locomotion — yes |
| 149 | `world-strafe-left.vrma` | Apache-2.0 | Overte | `walk_left.fbx` | 1.13s | 50 | locomotion — yes |
| 150 | `world-strafe-right-fast.vrma` | Apache-2.0 | Overte | `walk_right_fast.fbx` | 0.67s | 45 | locomotion — yes |
| 151 | `world-strafe-right-jog.vrma` | Apache-2.0 | Overte | `jog_right.fbx` | 0.67s | 48 | locomotion — yes |
| 152 | `world-strafe-right-run.vrma` | Apache-2.0 | Overte | `run_fast_right.fbx` | 0.63s | 22 | locomotion — yes |
| 153 | `world-strafe-right.vrma` | Apache-2.0 | Overte | `walk_right.fbx` | 1.13s | 46 | locomotion — yes |
| 154 | `world-turn-left.vrma` | Apache-2.0 | Overte | `turn_left.fbx` | 1.10s | 20 | locomotion — yes |
| 155 | `world-turn-right.vrma` | Apache-2.0 | Overte | `turn_right.fbx` | 1.00s | 29 | locomotion — yes |
| 156 | `world-walk-back-fast.vrma` | Apache-2.0 | Overte | `walk_bwd_fast.fbx` | 0.93s | 48 | locomotion — yes |
| 157 | `world-walk-back.vrma` | Apache-2.0 | Overte | `walk_bwd.fbx` | 1.23s | 51 | locomotion — yes |
| 158 | `world-walk-fast.vrma` | Apache-2.0 | Overte | `walk_fwd_fast.fbx` | 0.87s | 22 | locomotion — yes |
| 159 | `world-walk-slow.vrma` | Apache-2.0 | Overte | `walk_short_fwd.fbx` | 1.30s | 48 | locomotion — yes |
| 160 | `world-walk-start.vrma` | Apache-2.0 | Overte | `idle_to_walk.fbx` | 0.40s | 54 | locomotion — yes |
| 161 | `world-walk-stop-2.vrma` | Apache-2.0 | Overte | `settle_to_idle02.fbx` | 1.27s | 20 | idle — yes |
| 162 | `world-walk-stop-3.vrma` | Apache-2.0 | Overte | `settle_to_idle03.fbx` | 1.97s | 20 | idle — yes |
| 163 | `world-walk-stop-4.vrma` | Apache-2.0 | Overte | `settle_to_idle04.fbx` | 2.70s | 21 | idle — yes |
| 164 | `world-walk-stop-small.vrma` | Apache-2.0 | Overte | `settle_to_idle_small.fbx` | 1.27s | 50 | idle — yes |
| 165 | `world-walk-stop.vrma` | Apache-2.0 | Overte | `settle_to_idle.fbx` | 1.90s | 50 | idle — yes |
| 166 | `world-walk.vrma` | Apache-2.0 | Overte | `walk_fwd.fbx` | 1.00s | 21 | locomotion — yes |

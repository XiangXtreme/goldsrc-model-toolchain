# Physics Stress Prompts

Use this reference when a user wants to stress-test complex Blender physics authoring and GoldSrc MDL export. 这些 prompt 只规定可观察结果，不规定固定网格拓扑、求解器、参数或视觉风格。Let the author choose a suitable Blender construction and explain the tradeoff.

## Shared Prompt Prefix

```text
使用 $build-goldsrc-models，基于 Blender 5.2 LTS 和 live Blender MCP 制作一个自包含 GoldSrc MDL。

请先判断该效果适合使用刚体、约束、布料、软体、骨骼链、分段网格或这些方案的组合。选择能够可靠烘焙并导出到 GoldSrc MDL 的方案；如果 Blender 原生模拟不能直接导出，请采用合理的可导出近似，并明确说明保留了哪些视觉和物理特征。

不要用手工逐帧轨迹替代失败的物理求解，不要在检测到碰撞后脚本化制造后续运动，不要运行时生成或删除几何体。GoldSrc 不负责运行时物理，所有效果必须预烘焙到 MDL 动画中。

请检查关键帧的视觉结果，并同时输出客观证据：首次接触、断裂或脱落顺序、碰撞响应、穿透、运动所有权、最终稳定状态和动画传递误差。

除非需求明确给出数值，不要把艺术判断强行转化成固定阈值。最终只交付一个自包含 MDL。
```

## Choose The Authoring Representation

Choose by the requested observable behavior. Keep the authoring representation separate from the final GoldSrc representation.

| Effect | Suitable Blender route | GoldSrc export route | Main trap |
|---|---|---|---|
| Walls, boxes, stones, debris | Rigid bodies with convex or mesh proxies | Pre-cut meshes driven by baked piece bones | Early motion, proxy penetration, and scripted contact release |
| Rope, chain, cable | Rigid links, a bone chain, or a hybrid source simulation | Segmented mesh or exportable bone chain | Blender curves and constraints do not become runtime rope physics |
| Cloth, banner, soft sheet | Cloth/soft-body source solve or a bone/segment approximation | Low-frequency deformation bones or segmented panels | Direct cloth modifiers are not a reliable GoldSrc animation representation |
| Hinged doors and mechanisms | Explicit kinematic driver plus solver-owned passive bodies | Baked local bone rotations and translations | Mixing driver keyframes into non-driver rigid-body motion |
| Multi-stage fracture | Pre-cut render pieces, constraints, supports, and fixed collision collections | One assembled frame-0 mesh set with later baked transforms | Contact-time toggles and a second rebake after observing contact |

For rope or cloth, a lower-fidelity exportable approximation is acceptable when it preserves the requested silhouette, contact order, attachment behavior, and final state. Record the approximation in `intent.assumptions`; do not claim that the MDL contains runtime cloth or rope simulation.

## Common Traps

- Do not tune only the break threshold. Separate normal travel loads from impact loads using support topology, mass, height, or obstacle placement first.
- Keep visible cuts flush at frame 0 while hidden collision proxies have measured non-overlap. A render mesh and a collision proxy do not need identical boundaries.
- Create rigid-body membership, constraints, and collections before solving where possible. After changes, update the dependency graph once before evaluating frames.
- Initialize gravity, scene frame bounds, and point-cache bounds explicitly for each independent case. A live Blender session can retain the previous case's world settings.
- Audit constraint count, connected components, and body degree before solving. Use dense-topology findings as warnings and recovery guidance, not as a universal ban on expressive mechanisms.
- Tear down constraints and rigid bodies before removing their objects or meshes. A crash in Bullet sorting, island traversal, or frame evaluation is a lifecycle failure until source inspection proves otherwise.
- Transfer evaluated matrices through bone rest and parent spaces. Do not assign world-space deltas directly to `PoseBone.matrix_basis` or key only the first and last frame.
- Keep motion ownership auditable: explicitly declared kinematic drivers may be keyed; released non-drivers must be sampled from evaluated Blender matrices.
- For declared drivers, support location or angular targets through one shared helper and record the release frame. Remove scene-specific driver monkey-patches so hinged and swinging cases exercise the same ownership audit.
- Save the author checkpoint at the declared playback start after rendering review frames; rendering the settled frame must not leave the saved Blend parked at the end.
- Treat the independent SourceIO-derived GoldSrc-only reader as a geometry, texture, skin-family, and embedded-sequence readback tool. It must create Blender Actions for embedded v10 sequences and preserve all skin-family metadata; missing data is a regression. External sequence groups remain an explicit contract limitation.
- Inspect author renders and MDL round-trip renders independently. Nonblank pixels do not prove readable contact, fracture, rebound, or settlement.

## Pressure Test Prompts

Prepend the shared prefix to one prompt at a time. Use a fresh artifact directory and an independent contract for each case.

### 1. Pendulum Ball Breaks A Wall

```text
一根绳子从高处悬挂一个沉重球体。球体受初始摆动影响，撞向前方砖墙。第一次撞墙前，绳子和球体必须保持连接，墙体不能提前破坏。首次撞击后，墙体破成多个大小不同的碎块，部分碎块向前飞出，部分掉落并撞击地面。球体撞墙后反弹，绳子重新受力并继续摆动，最终球体、绳子和所有碎块稳定。

绳子可以使用骨骼链、分段网格或其他适合导出的方案。请重点处理绳体拉伸、摆锤碰撞、墙体断裂顺序、碎块穿透和最终稳定。
```

Tests rope representation, constraint lifetime, impact rebound, fracture ordering, and mixed-body settlement.

### 2. Suspended Bridge Collapse

```text
制作一座带木板、侧向绳索和悬挂支点的简易吊桥。一个滚动或摆动的重物撞击桥的一端，桥板和连接件不能在撞击前提前断裂。撞击后，桥板按照受力传播逐段脱落或翻转，部分木板相互碰撞并撞到桥墩，绳索和支点产生可见的拉扯或松脱，最后所有部件停止运动。

不要只制作一条预设的坍塌路径。请让桥体结构、约束和碰撞关系决定断裂顺序，并选择适合 GoldSrc 导出的骨骼或分段网格表达。
```

Tests staged contacts, hinges, structural failure, contact dependencies, and checkpoint recovery.

### 3. Cloth Awning Tears And Wraps

```text
制作一块由多个挂点悬挂的厚布幔。一个高速运动的物体从侧面撞击或扫过布幔，布幔先发生明显变形，之后部分挂点依次失效，布幔绕过或贴住附近的立柱，最后落到地面并稳定。撞击物也要产生合理的反弹或减速。

请判断原生 Cloth 或 Soft Body 是否适合直接作为导出结果。如果不能可靠导出，请使用骨骼链、低频变形骨骼、分段布片或其他可导出的近似，同时保持布幔轮廓、挂点脱落顺序和主要碰撞关系。
```

Tests non-rigid strategy selection, exportable deformation, self-collision, attachment release, and visual acceptance.

### 4. Spring Latch And Hinged Door

```text
制作一个带弹簧或拉杆的机械锁扣，以及一扇由铰链连接的重门。一个物体撞击锁扣后，锁扣先发生局部运动，门体随后旋转打开或脱落；门体撞击旁边的箱体，箱体翻倒，内部若干物件继续滚动并撞到挡板。所有部件最终稳定。

请区分明确的运动驱动对象和由刚体求解产生运动的对象。保留必要的机械约束和层级关系，避免把所有运动都写成手工 Action。最终动画必须正确传递旋转、父子空间和铰链运动。
```

Tests driver ownership, parent/rest-space transfer, hinge rotation, chained contacts, and animation compression.

### 5. Mixed Objects On A Stepped Run

```text
制作一段带多个台阶、斜面、狭窄通道和挡板的坡道。若干质量、形状和尺寸不同的木箱、圆桶和石块从不同高度开始运动。它们需要相互碰撞，部分物体从台阶边缘翻落，部分撞到挡板后改变方向，最后全部停在坡道下方或挡板附近。

请自行选择合适的碰撞形状、质量、摩擦和恢复参数，并建立足够长的稳定窗口。不要为了缩短模拟而降低碰撞质量、采样密度或最大帧数；如果动画预算过高，请先分析预算，再选择不破坏时长和关键接触的降采样方案。
```

Tests many bodies, mixed collision shapes, long settlement windows, animation budgets, and performance controls.

### 6. Ceramic Pot Shatters On Steps

```text
制作一个放置在台阶或货架上的陶罐。一个滚动物体撞击陶罐，首次接触前陶罐必须完整，撞击后陶罐破成多个大小不同、形状不规则的碎片。碎片继续沿台阶滚落，部分相互碰撞，部分撞到金属托盘后反弹，最后全部稳定。初始完整陶罐不能出现明显拼接缝或内部辅助物体。

请分别处理可见陶罐网格、隐藏碰撞代理、断裂约束和最终骨骼动画。材质、UV 和亮度必须在作者渲染与 MDL 回读中保持一致。
```

Tests fracture topology, seamless frame-0 presentation, collision proxies, material brightness, MDL readback, and delivery whitelists.

## Iterate Without Overfitting

After each case, record separate findings for visual presentation, physics, Blender lifecycle, export, compiler, SourceIO, and pipeline caching. A failed case must not be repaired by silently weakening its requirement.

Only promote a fix into the Skill when it is reusable across independent effects or is supported by a source-level finding. Add a unit test, fixture, or regression for every shared code or validation change. Do not add object-name, scene-name, or frame-number exceptions for a single prompt.

When Blender crashes, inspect the crash log and matching Blender/Bullet source before changing physics parameters. When a case passes, retain the report and checkpoint as evidence, and use `reuse_report` only when the upstream fingerprint is unchanged. Run the full test suite, Skill validation, package audit, and the next prompt from a clean artifact root.

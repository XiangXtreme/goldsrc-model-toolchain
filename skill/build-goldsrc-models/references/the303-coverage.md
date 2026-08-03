# The303 GoldSrc 模型教程 Blender 5.2 证据覆盖

模型相关页面：34。范围仅限 Blender 模型制作、导出、编译和回读，不包含地图场景搭建。覆盖状态证明执行证据；“知识入口”才是后续创作应读取的蒸馏结果。

## 状态统计

| 状态 | 页面数 |
|---|---:|
| document_review_only | 1 |
| partial_model_scope_only | 1 |
| partial_reference_coverage | 3 |
| validated_blender_equivalent | 29 |

## 页面证据与知识入口

| 页面 | 状态 | 原工具 | Blender MCP | 编译 | 回读 | 知识入口 | 说明 |
|---|---|---|---|---|---|---|---|
| tutorials__gold_mdl.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [静态制作](workflow-static-materials.md#static-models-and-materials) | Blender 5.2 equivalent workflow validated; legacy DCC was not launched. |
| tutorials__gold_mdl_3ds.md | validated_blender_equivalent | 3ds_max | 是 | 是 | 是 | [静态制作](workflow-static-materials.md#static-models-and-materials) | Blender equivalent validated; 3ds Max was not launched. |
| tutorials__gold_mdl_3ds_gz.md | validated_blender_equivalent | 3ds_max | 是 | 是 | 是 | [静态制作](workflow-static-materials.md#static-models-and-materials) | Blender equivalent validated; 3ds Max was not launched. |
| tutorials__gold_mdl_3ds_wu.md | validated_blender_equivalent | 3ds_max | 是 | 是 | 是 | [静态制作](workflow-static-materials.md#static-models-and-materials) | Blender equivalent validated; 3ds Max was not launched. |
| tutorials__gold_mdl_3ds_ww.md | validated_blender_equivalent | 3ds_max | 是 | 是 | 是 | [静态制作](workflow-static-materials.md#static-models-and-materials) | Blender equivalent validated; 3ds Max was not launched. |
| tutorials__gold_mdl_blend.md | validated_blender_equivalent | blender | 是 | 是 | 是 | [静态制作](workflow-static-materials.md#static-models-and-materials) | Blender 5.2 equivalent workflow validated. |
| tutorials__gold_mdl_blend279.md | validated_blender_equivalent | blender | 是 | 是 | 是 | [静态制作](workflow-static-materials.md#static-models-and-materials) | Blender 2.79 steps were translated and validated in Blender 5.2. |
| tutorials__gold_mdl_chrome.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [材质](workflow-static-materials.md#create-uvs-and-materials), [indexed BMP](pitfalls.md#tex-indexed) | Indexed textures, special flags, normals, and previews validated. |
| tutorials__gold_mdl_comp.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [工具链](toolchain.md#runtime-surface), [编译不等于验收](pitfalls.md#compiler-success) | Export, compile, inspection, and readback validated. |
| tutorials__gold_mdl_dynpc.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [角色动画](workflow-animation-characters.md#animation-and-character-models) | Root motion, activities, events, hitboxes, attachments, mouth, and sequences validated. |
| tutorials__gold_mdl_fix.md | partial_reference_coverage | mixed_or_not_applicable | 是 | 是 | 是 | [导入修复](workflow-import-repair.md#import-repair-and-troubleshooting), [坑点库](pitfalls.md#goldsrc-blender-pitfalls) | Representative failures reproduced; the source catalog is broader than one fixture suite. |
| tutorials__gold_mdl_frag.md | validated_blender_equivalent | fragmotion | 是 | 是 | 是 | [导入修复](workflow-import-repair.md#import-repair-and-troubleshooting) | Blender equivalent validated; fragMOTION was not launched. |
| tutorials__gold_mdl_khed.md | validated_blender_equivalent | khed | 是 | 是 | 是 | [静态制作](workflow-static-materials.md#static-models-and-materials) | Blender equivalent validated; kHED was not launched. |
| tutorials__gold_mdl_leg.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [材质](workflow-static-materials.md#create-uvs-and-materials), [masked](pitfalls.md#tex-masked) | Indexed textures, flags, normals, and previews validated. |
| tutorials__gold_mdl_maya.md | validated_blender_equivalent | maya | 是 | 是 | 是 | [静态制作](workflow-static-materials.md#static-models-and-materials) | Blender equivalent validated; Maya was not launched. |
| tutorials__gold_mdl_milk.md | validated_blender_equivalent | milkshape3d | 是 | 是 | 是 | [静态制作](workflow-static-materials.md#static-models-and-materials) | Blender equivalent validated; MilkShape 3D was not launched. |
| tutorials__gold_mdl_repair.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [导入修复](workflow-import-repair.md#import-repair-and-troubleshooting), [UV](pitfalls.md#uv-compensation), [权重](pitfalls.md#single-weight) | UV compensation, island repair, weights, normals, soft bone, hitbox, and attachment evidence exists. |
| tutorials__gold_mdl_tex_links.md | document_review_only | mixed_or_not_applicable | 否 | 否 | 否 | [材质](workflow-static-materials.md#create-uvs-and-materials) | Primarily an external-link/reference index rather than an executable workflow. |
| tutorials__gold_qc.md | partial_reference_coverage | mixed_or_not_applicable | 是 | 是 | 是 | [合同](model-contract.md#required-shape), [格式限制](goldsrc-constraints.md#animation-and-model-structure) | Representative QC commands executed; full encyclopedia not exhaustively instantiated. |
| tutorials__gold_research.md | partial_reference_coverage | mixed_or_not_applicable | 是 | 是 | 是 | [静态材质](workflow-static-materials.md#static-models-and-materials), [角色结构](workflow-animation-characters.md#add-character-metadata) | High-to-low bake, planes, foliage, normals, bodygroups, skins, and low-poly shells validated. |
| tutorials__sven_fullbright.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [材质](workflow-static-materials.md#create-uvs-and-materials), [indexed BMP](pitfalls.md#tex-indexed) | Indexed textures, fullbright flag, normals, and previews validated. |
| tutorials__gold_mdl_aniprop_3ds.md | validated_blender_equivalent | 3ds_max | 是 | 是 | 是 | [动画重映射](workflow-animation-characters.md#author-actions), [骨骼空间](pitfalls.md#bone-space) | Different bones/proportions remapped and baked; 3ds Max was not launched. |
| tutorials__gold_mdl_aniprop_blend.md | validated_blender_equivalent | blender | 是 | 是 | 是 | [动画重映射](workflow-animation-characters.md#author-actions) | Different bones/proportions remapped, corrected, exported, and compiled. |
| tutorials__gold_mdl_aniremap.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [动画重映射](workflow-animation-characters.md#author-actions), [Action](pitfalls.md#action-channelbags) | Explicit bone map and baked target Action validated. |
| tutorials__gold_mdl_aniremap_frag.md | validated_blender_equivalent | fragmotion | 是 | 是 | 是 | [动画重映射](workflow-animation-characters.md#author-actions) | Blender equivalent validated; fragMOTION was not launched. |
| tutorials__gold_mdl_phys.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [物理烘焙](workflow-physics-baking.md#physics-and-exportable-effects), [生命周期](pitfalls.md#physics-lifecycle) | Blender rigid-body simulation baked to piece bones, exported, and compiled. |
| tutorials__gold_mdl_phys_3ds.md | validated_blender_equivalent | 3ds_max | 是 | 是 | 是 | [物理烘焙](workflow-physics-baking.md#physics-and-exportable-effects) | Blender physics equivalent validated; 3ds Max was not launched. |
| tutorials__gold_mdl_phys_blend.md | validated_blender_equivalent | blender | 是 | 是 | 是 | [物理烘焙](workflow-physics-baking.md#physics-and-exportable-effects), [骨骼空间](pitfalls.md#bone-space) | Blender rigid-body-to-bone workflow validated. |
| tutorials__gold_mdl_phys_blend279.md | validated_blender_equivalent | blender | 是 | 是 | 是 | [物理烘焙](workflow-physics-baking.md#physics-and-exportable-effects) | Blender 2.79 method translated and validated in Blender 5.2. |
| tutorials__gold_mdl_phys_map.md | partial_model_scope_only | mixed_or_not_applicable | 是 | 是 | 是 | [物理烘焙](workflow-physics-baking.md#export) | Model bake/export/compile validated; map entities remain outside scope. |
| tutorials__gold_flappyjaws.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [角色动画](workflow-animation-characters.md#animation-and-character-models) | Barney baseline, mouth, hitboxes, attachments, controllers, remap texture, and sequences validated. |
| tutorials__gold_player_mdl.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [角色动画](workflow-animation-characters.md#animation-and-character-models) | High/low body models, skeleton, metadata, textures, and 77 sequences validated. |
| tutorials__gold_player_sven_comp.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [角色导出](workflow-animation-characters.md#export-and-read-back), [工具链](toolchain.md#runtime-surface) | Sven player compilation and structural records validated. |
| tutorials__gold_remap.md | validated_blender_equivalent | mixed_or_not_applicable | 是 | 是 | 是 | [角色材质](workflow-animation-characters.md#add-character-metadata) | Player remap texture semantics validated with the model structure. |

## 已确认限制

- The independent SourceIO-derived reader imports embedded GoldSrc MDL v10 sequences into Blender Actions and preserves every skin-family table; external sequence groups remain unsupported unless explicitly declared.
- Legacy DCC-specific tutorials were validated through Blender equivalents, not by launching the original DCC applications.

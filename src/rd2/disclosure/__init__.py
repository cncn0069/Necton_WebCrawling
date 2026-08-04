"""정보공개 도메인의 **어휘**. 행위는 없다.

여기 있는 것은 조문·기관 분류·비밀등급처럼 여러 층이 함께 보는 상수와 그 상수를
읽는 순수 함수뿐이다. 수집도, 생성도, 렌더링도 하지 않는다.

**왜 따로 떼었나.** 이 어휘가 ``generators`` 안에 살던 동안 아래층이 위층을 향해
손을 뻗었다. ``augmentation.llm_augment``와 ``source_generation.c_track_templates``가
조문 정의 하나를 쓰려고 ``rd2.generators.clause_data``를 import했고, 그 두 줄 때문에
패키지 의존이 양방향이 되어 층을 세울 수 없었다 — ``generators``는 이미
``source_generation``과 ``augmentation``을 import하고 있었다.

어휘는 그것을 쓰는 누구보다도 아래에 있어야 한다. 이 패키지는 ``rd2.schema``
외에는 ``rd2`` 안의 무엇도 import하지 않으며, ``tests/test_layering.py``가 그것을
지킨다.
"""

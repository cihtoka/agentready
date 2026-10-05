# 페이지 구조와 메타 정보 가이드라인

## landmarks.why: 시맨틱 랜드마크가 필요한 이유
header, nav, main, footer 로 영역이 나뉘어 있으면 에이전트가 main 에서 본문 정보를 우선 읽고 nav 에서 이동 경로를 찾는다. 페이지를 div 로만 만들면 메뉴, 광고, 본문이 뒤섞여 잘못된 정보를 읽는다.

## landmarks.fix: 랜드마크 태그 적용 방법
div class="header", "gnb", "footer" 를 header, nav, footer 태그로 바꾸고 페이지 본문을 main 하나로 감싼다. 태그를 바꾸기 어려운 레이아웃은 role 속성으로 대체할 수 있다.

## landmarks.example: 랜드마크 예시
나쁜 예는 <div class="gnb">…</div><div class="contents">…</div> 이다. 좋은 예는 <nav>…</nav><main>…</main><footer>…</footer> 구조다.

## headings.why: 제목 구조가 중요한 이유
제목 계층은 에이전트가 페이지의 주제와 섹션 구조를 요약하는 기준이다. h1 이 없거나 여러 개이고 레벨을 건너뛰면 상품명을 찾기 어렵다.

## headings.fix: 제목 태그 정리 방법
페이지당 h1 은 하나만 두고 상품명을 넣는다. h2, h3 는 순서를 건너뛰지 않는다. 글자 크기만 키운 div 나 font 태그는 제목 태그로 바꾼다.

## headings.example: 제목 구조 예시
나쁜 예는 <div class="tit">무선 청소기</div> 처럼 제목 태그가 없거나 h1 다음에 곧바로 h3 가 나오는 경우다. 좋은 예는 <h1>무선 청소기</h1><h2>상품 정보</h2><h3>배송 안내</h3> 순서다.

## meta.why: 메타 정보가 필요한 이유
title, description, Open Graph 는 에이전트가 페이지를 열기 전에 내용을 판단하는 근거다. untitled 같은 기본 제목은 신뢰도가 낮은 페이지로 분류되는 원인이 된다.

## meta.fix: 메타 태그 보강 방법
페이지마다 고유하고 의미 있는 title 을 쓰고, 20자 이상의 meta description 과 og:title, og:description 을 추가한다. 상품 페이지는 상품명과 핵심 특징을 담는다.

## meta.example: 메타 태그 예시
나쁜 예는 <title>untitled</title> 이다. 좋은 예는 <title>원목 사이드 테이블 - 나무결 스토어</title> 와 <meta name="description" content="국산 오크 원목으로 만든 사이드 테이블"> 그리고 og:title 태그를 함께 두는 것이다.

## alt.why: 이미지 대체 텍스트가 필요한 이유
에이전트는 이미지를 직접 해석하지 못하는 경우가 많아 alt 텍스트가 이미지의 유일한 정보다. alt 가 없으면 상품 이미지가 무엇인지 알 수 없다.

## alt.fix: alt 텍스트 작성 방법
모든 상품 이미지에 상품을 설명하는 alt 를 넣는다. 장식용 이미지는 빈 alt 와 role="presentation" 으로 구분해 에이전트가 건너뛰게 한다.

## alt.example: alt 예시
나쁜 예는 <img src="p1.jpg"> 처럼 alt 가 없는 경우다. 좋은 예는 <img src="p1.jpg" alt="오크 원목 사이드 테이블 정면"> 이다.

## machine_files.why: robots.txt와 llms.txt가 필요한 이유
robots.txt 는 에이전트의 접근 허용 범위를 알리고, llms.txt 는 사이트 개요와 주요 페이지를 한 번에 알려 준다. 둘 다 없으면 에이전트가 구조를 추측하며 불필요한 탐색을 한다. robots.txt 가 전체 차단이면 접근 자체가 막힌다.

## machine_files.fix: robots.txt와 llms.txt 작성 방법
사이트 루트에 robots.txt 를 두고 허용 정책을 명시한다. llms.txt 에는 사이트 이름, 한 줄 설명, 주요 페이지 링크를 마크다운으로 적는다. 허용 범위는 운영 정책에 맞게 정해야 한다.

## machine_files.example: robots.txt와 llms.txt 예시
robots.txt 는 User-agent: * 와 Allow: / 로 허용을 밝힌다. llms.txt 는 # 나무결 스토어, > 원목 가구 전문몰, ## 주요 페이지, - [베스트](product.html) 형태로 작성한다.

## general.flow: 쇼핑 에이전트의 작업 흐름
AI 쇼핑 에이전트는 검색, 상품 선택, 가격 확인, 장바구니 담기, 장바구니 도달 순서로 진행한다. 한 단계라도 표준 태그로 확인되지 않으면 그 지점에서 멈춘다.

## general.priority: 수정 우선순위와 승인
수정은 점수 개선 효과가 큰 항목부터 한다. 표준 클릭 요소와 구조화 데이터의 비중이 가장 크다. DOM 구조 변경, 재고 추정, 크롤링 정책 공개는 고위험 수정이므로 사람의 승인 후 적용한다.

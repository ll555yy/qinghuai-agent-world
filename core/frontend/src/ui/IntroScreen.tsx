import { useUiStore } from '../state/uiStore'

export function IntroScreen() {
  const setPhase = useUiStore((state) => state.setPhase)
  return (
    <main className="app-shell intro-screen">
      <section className="intro-card">
        <p className="eyebrow">青槐巷 · 七日方案期</p>
        <h1>慎之旧书店，只剩七天。</h1>
        <p>
          铺面将在三十天后被收回。林慧兰、沈星遥、赵磊、陈月和周慎之各有自己的打算，
          但他们只有七天时间形成一份方案。
        </p>
        <p>
          这里没有搬运、收集或点击完成的任务。你能做的是观察他们的行动，加入聊天，自由说出你的想法。
        </p>
        <button type="button" onClick={() => setPhase('agenda')}>
          查看可选立场
        </button>
      </section>
    </main>
  )
}

<script setup lang="ts">
import { computed } from 'vue'
import { serverDate } from '../utils/dateTime'

const props = defineProps<{
  kind: 'game' | 'invoice' | 'story' | 'paid_media' | 'giveaway' | 'giveaway_results'
  content: Record<string, any>
}>()

function formatTelegramAmount(amount: unknown, currency: unknown) {
  const numeric = Number(amount || 0)
  const code = String(currency || '').toUpperCase()
  if (code === 'XTR') return `${numeric.toLocaleString('zh-CN')} ⭐`
  if (!code) return numeric.toLocaleString('zh-CN')
  try {
    const formatter = new Intl.NumberFormat('zh-CN', { style: 'currency', currency: code })
    const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2
    return formatter.format(numeric / (10 ** digits))
  } catch {
    return `${numeric.toLocaleString('zh-CN')} ${code}`
  }
}

function formatDate(value: unknown) {
  if (!value) return '时间未知'
  const date = serverDate(String(value))
  if (Number.isNaN(date.getTime())) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

const giveawayQuantity = computed(() => Number(props.content.quantity || props.content.winners_count || 0))
const giveawayPrize = computed(() => {
  if (props.content.prize_description) return String(props.content.prize_description)
  if (props.content.stars) return `${Number(props.content.stars).toLocaleString('zh-CN')} Telegram Stars`
  if (props.content.months) return `${props.content.months} 个月 Telegram Premium`
  return 'Telegram 奖品'
})
</script>

<template>
  <section v-if="kind === 'game'" class="archive-special game-card">
    <div class="game-preview" aria-hidden="true">
      <svg viewBox="0 0 64 64"><path d="M22 24h20c8 0 14 6 15 14l1 8c1 7-7 11-12 6l-7-7H25l-7 7c-5 5-13 1-12-6l1-8c1-8 7-14 15-14Z"/><path d="M18 33v10m-5-5h10m20-3h.1m6 6h.1"/></svg>
      <span>游戏归档</span>
    </div>
    <strong>{{ content.title || content.short_name || 'Telegram 游戏' }}</strong>
    <p v-if="content.description">{{ content.description }}</p>
    <small>交互内容仅可在 Telegram 中运行</small>
  </section>

  <section v-else-if="kind === 'invoice'" class="archive-special invoice-card">
    <span class="special-eyebrow">付款信息</span>
    <strong>{{ content.title || 'Telegram 付款单' }}</strong>
    <p v-if="content.description">{{ content.description }}</p>
    <div class="invoice-total">
      <span>{{ formatTelegramAmount(content.total_amount, content.currency) }}</span>
      <i v-if="content.test">测试付款单</i>
      <i v-if="content.receipt_message_id">已付款</i>
    </div>
    <small v-if="content.shipping_address_requested">付款时需要提供收货地址</small>
    <small class="readonly-hint">归档副本不能发起付款</small>
  </section>

  <section v-else-if="kind === 'story'" :class="['archive-special', 'story-card', `is-${content.state || 'unavailable'}`]">
    <div class="story-frame">
      <svg viewBox="0 0 64 64"><path d="M17 8h30a9 9 0 0 1 9 9v30a9 9 0 0 1-9 9H17a9 9 0 0 1-9-9V17a9 9 0 0 1 9-9Z"/><path d="m15 45 11-12 8 8 6-7 10 11M42 22h.1"/></svg>
      <strong>{{ content.state === 'expired' ? 'Story 已过期' : content.state === 'available' ? 'Story 已归档' : 'Story 不可用' }}</strong>
      <span>Story #{{ content.story_id }}</span>
    </div>
    <p v-if="content.caption">{{ content.caption }}</p>
    <small v-if="content.via_mention">通过提及分享</small>
    <small v-if="content.expire_date">有效期至 {{ formatDate(content.expire_date) }}</small>
  </section>

  <section v-else-if="kind === 'paid_media'" class="archive-special paid-media-card">
    <div class="paid-media-preview" :data-count="Math.max(1, Number(content.item_count || 0))">
      <span v-for="index in Math.min(4, Math.max(1, Number(content.item_count || 0)))" :key="index"><svg viewBox="0 0 24 24"><path d="M7 10V8a5 5 0 0 1 10 0v2m-11 0h12v10H6V10Z"/></svg></span>
      <strong>{{ content.purchased ? '已购买的媒体' : `${Number(content.stars_amount || 0).toLocaleString('zh-CN')} ⭐` }}</strong>
    </div>
    <div class="paid-media-copy">
      <strong>{{ content.item_count || 0 }} 项付费媒体</strong>
      <small>{{ content.purchased ? '媒体在备份时已可访问' : '备份只保留了锁定内容的描述信息' }}</small>
    </div>
  </section>

  <section v-else-if="kind === 'giveaway'" class="archive-special giveaway-card">
    <div class="giveaway-hero"><span>🎁</span><i>×{{ giveawayQuantity }}</i></div>
    <div class="giveaway-section"><strong>抽奖奖品</strong><p>{{ giveawayPrize }}</p></div>
    <div class="giveaway-divider"><span>赠送给</span></div>
    <div class="giveaway-section"><strong>{{ giveawayQuantity }} 位获奖者</strong><p>{{ content.channel_ids?.length || 1 }} 个参与频道<span v-if="content.only_new_subscribers"> · 仅限新订阅者</span></p></div>
    <div v-if="content.countries?.length" class="country-list"><span v-for="country in content.countries" :key="country">{{ country }}</span></div>
    <div class="giveaway-section"><strong>开奖时间</strong><p>{{ formatDate(content.until_date) }}</p></div>
    <small class="readonly-hint">这是抽奖活动的只读归档</small>
  </section>

  <section v-else class="archive-special giveaway-card giveaway-results-card">
    <div class="giveaway-hero"><span>🎉</span><i>×{{ giveawayQuantity }}</i></div>
    <div class="giveaway-section"><strong>{{ content.refunded ? '抽奖已退款' : '获奖者已经选出' }}</strong><p>{{ giveawayPrize }}</p></div>
    <div v-if="content.winner_ids?.length" class="winner-list" aria-label="获奖者 Telegram ID"><span v-for="winner in content.winner_ids" :key="winner">{{ winner }}</span></div>
    <p v-if="content.additional_peers_count">另有 {{ content.additional_peers_count }} 位获奖者</p>
    <p v-if="content.unclaimed_count">{{ content.unclaimed_count }} 份奖品尚未领取</p>
    <small>{{ formatDate(content.until_date) }} · 抽奖结果归档</small>
  </section>
</template>

<style scoped>
.archive-special { min-width: min(21rem, calc(100vw - 5.5rem)); max-width: 25rem; box-sizing: border-box; color: inherit; }
.archive-special strong, .archive-special p { overflow-wrap: anywhere; }
.archive-special p { margin: 0; font-size: .76rem; line-height: 1.38; }
.archive-special small { color: #6e7c8e; font-size: .66rem; line-height: 1.35; }
.special-eyebrow { color: #268acb; font-size: .65rem; font-weight: 750; }
.game-card { display: grid; gap: .34rem; }
.game-card > strong { font-size: .86rem; }
.game-preview { position: relative; aspect-ratio: 16 / 9; margin: -.125rem -.125rem .12rem; overflow: hidden; border-radius: .65rem; display: grid; place-items: center; color: white; background: radial-gradient(circle at 75% 18%, rgba(255,255,255,.24), transparent 26%), linear-gradient(145deg,#4a55c9,#262f80 58%,#151a50); }
.game-preview::after { content: ''; position: absolute; inset: 0; background-image: linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px); background-size: 1.5rem 1.5rem; }
.game-preview svg { z-index: 1; width: 3.6rem; fill: rgba(255,255,255,.12); stroke: white; stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; }
.game-preview span { z-index: 1; position: absolute; left: .65rem; bottom: .5rem; padding: .18rem .38rem; border-radius: .35rem; background: rgba(0,0,0,.36); font-size: .62rem; font-weight: 700; }
.invoice-card { display: grid; gap: .32rem; }
.invoice-card > strong { font-size: .88rem; }
.invoice-total { margin-top: .22rem; min-height: 2.3rem; padding: .45rem .58rem; border-radius: .62rem; display: flex; flex-wrap: wrap; align-items: center; gap: .38rem; color: white; background: linear-gradient(135deg,#319de5,#1d78bb); }
.invoice-total span { margin-right: auto; font-size: .96rem; font-weight: 780; }
.invoice-total i { padding: .16rem .34rem; border-radius: 99px; background: rgba(255,255,255,.18); font-size: .58rem; font-style: normal; font-weight: 700; }
.readonly-hint { display: block; margin-top: .12rem; }
.story-card { width: 12rem; min-width: 12rem; display: grid; gap: .35rem; }
.story-frame { position: relative; aspect-ratio: 192 / 344; overflow: hidden; border-radius: .72rem; display: grid; place-content: center; justify-items: center; gap: .45rem; color: white; background: radial-gradient(circle at 50% 32%,rgba(121,151,255,.3),transparent 29%),linear-gradient(160deg,#353f67,#1c2235 65%,#111520); }
.story-frame::before { content: ''; position: absolute; inset: .28rem; border: 2px solid rgba(255,255,255,.34); border-radius: .58rem; }
.story-frame svg { width: 3.3rem; fill: none; stroke: rgba(255,255,255,.78); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.story-frame strong { font-size: .78rem; }
.story-frame span { font-size: .63rem; opacity: .72; }
.story-card.is-expired .story-frame { filter: saturate(.25); }
.paid-media-card { display: grid; gap: .5rem; }
.paid-media-preview { position: relative; aspect-ratio: 16 / 10; padding: .28rem; box-sizing: border-box; overflow: hidden; border-radius: .68rem; display: grid; grid-template-columns: repeat(2,1fr); gap: .2rem; background: #222a38; }
.paid-media-preview > span { min-height: 0; display: grid; place-items: center; color: rgba(255,255,255,.65); background: linear-gradient(145deg,#536178,#303949); }
.paid-media-preview > span:only-of-type { grid-column: 1 / -1; }
.paid-media-preview svg { width: 1.65rem; fill: none; stroke: currentColor; stroke-width: 1.7; }
.paid-media-preview > strong { position: absolute; left: 50%; top: 50%; transform: translate(-50%,-50%); padding: .34rem .62rem; border-radius: 99px; color: white; background: rgba(15,18,25,.72); backdrop-filter: blur(10px); font-size: .72rem; white-space: nowrap; }
.paid-media-copy { display: grid; gap: .08rem; }
.paid-media-copy strong { font-size: .82rem; }
.giveaway-card { display: grid; justify-items: center; gap: .62rem; text-align: center; }
.giveaway-hero { position: relative; width: 6rem; height: 5.2rem; display: grid; place-items: center; }
.giveaway-hero > span { font-size: 4.6rem; line-height: 1; filter: drop-shadow(0 .18rem .22rem rgba(0,0,0,.16)); }
.giveaway-hero > i { position: absolute; left: 50%; bottom: 0; transform: translateX(-50%); padding: .08rem .45rem; border: 1px solid var(--bubble-color, white); border-radius: 99px; color: white; background: #258ed2; font-size: .7rem; font-style: normal; font-weight: 800; }
.giveaway-section { display: grid; gap: .12rem; }
.giveaway-section strong { font-size: .8rem; }
.giveaway-divider { width: 100%; display: flex; align-items: center; gap: .4rem; color: #718092; font-size: .65rem; }
.giveaway-divider::before,.giveaway-divider::after { content: ''; height: 1px; flex: 1; background: rgba(105,123,143,.25); }
.country-list,.winner-list { display: flex; flex-wrap: wrap; justify-content: center; gap: .3rem; }
.country-list span,.winner-list span { padding: .18rem .4rem; border-radius: 99px; color: #287fb6; background: rgba(43,143,205,.11); font-size: .62rem; }
.giveaway-results-card > p { color: #63758a; }
:global(.archive-message-line.out .archive-special small),
:global(.archive-message-line.out .giveaway-divider),
:global(.archive-message-line.out .giveaway-results-card > p) { color: rgba(255,255,255,.72); }
:global(.archive-message-line.out .country-list span),
:global(.archive-message-line.out .winner-list span) { color: white; background: rgba(255,255,255,.14); }
</style>

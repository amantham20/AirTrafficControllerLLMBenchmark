import { chromium } from 'playwright'
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } })
await page.goto('http://localhost:8000/')
await page.waitForTimeout(8000)
// click the first aircraft callsign label
const plane = page.locator('g[data-callsign]').first()
if (await plane.count() > 0) await plane.click({ force: true })
await page.waitForTimeout(1000)
await page.screenshot({ path: '/tmp/claude-0/-home-user-AirTrafficControllerLLMBenchmark/3a5124fa-5396-5257-a71f-a43ea0ac0ffe/scratchpad/ui2.png' })
await browser.close()

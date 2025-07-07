const puppeteer = require("puppeteer-extra");
const StealthPlugin = require("puppeteer-extra-plugin-stealth");
puppeteer.use(StealthPlugin());

const url = process.argv[2];
const targetRiotID = process.argv[3]; // not used here but keep for compatibility

if (!url || !url.startsWith("http")) {
  console.error("❌ Please provide a valid Tracker.gg match URL.");
  process.exit(1);
}

(async () => {
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: '/usr/bin/google-chrome',
    defaultViewport: null,
    args: ["--no-sandbox", "--disable-setuid-sandbox"]
  });

  const page = await browser.newPage();
  await page.setUserAgent(
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
  );

  try {
    console.error(`⏳ Loading: ${url}`);
    await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 });

    console.error("✅ Page loaded. Waiting for scoreboard...");
    await page.waitForSelector(".st-content__item img[alt]", { timeout: 30000 });
    console.error("✅ Scoreboard detected. Parsing match data...");

    const data = await page.evaluate(() => {
      const knownMaps = [
        "Ascent", "Bind", "Breeze", "Fracture", "Haven", "Icebox", "Lotus",
        "Pearl", "Split", "Sunset", "Corrode", "Abyss"
      ];

      const toFloat = val => parseFloat(val.replace(/[+,%]/g, "")) || 0;
      const toInt = val => parseInt(val.replace(/[+,%]/g, ""), 10) || 0;

      // Match info
      const labels = Array.from(document.querySelectorAll(".trn-match-drawer__header-label"))
        .map(el => el.textContent.trim());
      const values = Array.from(document.querySelectorAll(".trn-match-drawer__header-value"))
        .map(el => el.textContent.trim());

      const mode = labels[0] || "";
      const mapText = values[0] || "";
      const map = knownMaps.includes(mapText) ? mapText : "Unknown";

      // Team scores and round count
      const redScore = parseInt(
        document.querySelector('.trn-match-drawer__header-value.valorant-color-team-1')?.textContent.trim()
      ) || 0;
      const blueScore = parseInt(
        document.querySelector('.trn-match-drawer__header-value.valorant-color-team-2')?.textContent.trim()
      ) || 0;
      const round_count = redScore + blueScore;

      // Players
      const rows = Array.from(document.querySelectorAll(".st-content__item"));
      const players = rows.map((row, index) => {
        const nameElement = row.querySelector(".trn-ign__username");
        const tagElement = row.querySelector(".trn-ign__discriminator");

        const rawName = nameElement?.textContent.trim() || "";
        const rawTag = tagElement?.textContent.trim() || "";
        if (!rawName || !rawTag) return null;

        const cleanName = rawName.endsWith('#') ? rawName.slice(0, -1) : rawName;
        const cleanTag = rawTag.startsWith('#') ? rawTag.slice(1) : rawTag;
        const name = `${cleanName}#${cleanTag}`;

        const agentImg = row.querySelector('.image > img[alt][src*="agents"]');
        const agent = agentImg?.getAttribute("alt")?.trim() || "?";

        const rankImg = row.querySelector(
          'img[alt*="Iron"], img[alt*="Bronze"], img[alt*="Silver"], ' +
          'img[alt*="Gold"], img[alt*="Platinum"], img[alt*="Diamond"], ' +
          'img[alt*="Ascendant"], img[alt*="Immortal"], img[alt*="Radiant"]'
        );
        const tier = rankImg?.getAttribute("alt") ?? "?";

        const cells = Array.from(row.querySelectorAll(".st-content__item-value"))
          .map(cell => cell.textContent.trim());

        const team = index < 5 ? "Red" : "Blue";

        // ACS is score column (assumed to be index 2)
        const acs = toInt(cells[2]);

        return {
          name,
          agent,
          team,
          tier,
          acs,
          score: acs, // alias for legacy
          kills: toInt(cells[3]),
          deaths: toInt(cells[4]),
          assists: toInt(cells[5]),
          plus_minus: cells[6] || "?",
          kd_ratio: toFloat(cells[7]),
          dda: cells[8] || "?",
          adr: toFloat(cells[9]),
          hs_pct: toFloat(cells[10]),
          kast_pct: cells[11] || "?",
          fk: toInt(cells[12]),
          fd: toInt(cells[13]),
          mk: toInt(cells[14])
        };
      }).filter(p => p !== null);

      // Sort players descending by ACS
      players.sort((a, b) => b.acs - a.acs);

      // ACS bonus points allocation for top 5
      const acsBonusPoints = [5, 4, 3, 2, 1];
      players.forEach((p, i) => {
        p.acs_bonus = i < 5 ? acsBonusPoints[i] : 0;
      });

      // Add team round-win points to each player
      players.forEach(p => {
        p.round_win_points = p.team === "Red" ? redScore : blueScore;
        p.total_points = p.acs_bonus + p.round_win_points;
      });

      return {
        map,
        mode,
        team1_score: redScore,
        team2_score: blueScore,
        round_count,
        players
      };
    });

    console.error("✅ Data parsed successfully.");
    console.error(`✅ ${data.map} match saved. ${data.players.length} players found.`);
    console.log(JSON.stringify(data, null, 2));

  } catch (err) {
    console.error("❌ Scraping failed:", err);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

const CONFIG = {
  input: 'Workforce Runtime Animation (standalone).html',
  frameDir: 'output/workforce-runtime-animation-frames',

  width: 1280,
  height: 720,
  deviceScaleFactor: 2,
  fps: 30,

  selector: 'svg[data-om-exportable-video-with-duration-secs]',
};

async function main() {
  const inputPath = path.resolve(CONFIG.input);
  const frameDir = path.resolve(CONFIG.frameDir);

  if (!fs.existsSync(inputPath)) {
    throw new Error(`Input file does not exist: ${inputPath}`);
  }

  fs.rmSync(frameDir, { recursive: true, force: true });
  fs.mkdirSync(frameDir, { recursive: true });

  const browser = await chromium.launch({
    headless: true,
  });

  try {
    const page = await browser.newPage({
      viewport: {
        width: CONFIG.width,
        height: CONFIG.height,
      },
      deviceScaleFactor: CONFIG.deviceScaleFactor,
    });

    await page.goto(pathToFileURL(inputPath).href, {
      waitUntil: 'networkidle',
      timeout: 60000,
    });

    await page.waitForSelector(CONFIG.selector, {
      timeout: 60000,
    });

    const stageInfo = await page.evaluate(
      ({ selector, width, height }) => {
        const svg = document.querySelector(selector);

        if (!svg) {
          throw new Error(`Cannot find animation SVG: ${selector}`);
        }

        const stageWrapper = svg.parentElement;
        const playerWrapper = stageWrapper?.parentElement;

        // Hide every player UI element except the stage wrapper.
        if (playerWrapper && stageWrapper) {
          for (const child of playerWrapper.children) {
            if (child !== stageWrapper) {
              child.style.display = 'none';
            }
          }
        }

        // Remove visual effects and transforms that can leak outside the stage.
        Object.assign(svg.style, {
          display: 'block',
          margin: '0',
          padding: '0',
          transform: 'none',
          boxShadow: 'none',
        });

        svg.setAttribute('width', String(width));
        svg.setAttribute('height', String(height));

        if (stageWrapper) {
          Object.assign(stageWrapper.style, {
            flex: 'none',
            width: `${width}px`,
            height: `${height}px`,
            minWidth: `${width}px`,
            minHeight: `${height}px`,
            maxWidth: `${width}px`,
            maxHeight: `${height}px`,
            margin: '0',
            padding: '0',
            overflow: 'hidden',
          });
        }

        if (playerWrapper) {
          Object.assign(playerWrapper.style, {
            width: `${width}px`,
            height: `${height}px`,
            minWidth: `${width}px`,
            minHeight: `${height}px`,
            margin: '0',
            padding: '0',
            overflow: 'hidden',
          });
        }

        Object.assign(document.documentElement.style, {
          width: `${width}px`,
          height: `${height}px`,
          margin: '0',
          padding: '0',
          overflow: 'hidden',
          background: '#f4f3f0',
        });

        Object.assign(document.body.style, {
          width: `${width}px`,
          height: `${height}px`,
          margin: '0',
          padding: '0',
          overflow: 'hidden',
          background: '#f4f3f0',
        });

        const rect = svg.getBoundingClientRect();
        const duration = Number(
          svg.getAttribute(
            'data-om-exportable-video-with-duration-secs'
          )
        );

        return {
          duration,
          rect: {
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
          },
        };
      },
      {
        selector: CONFIG.selector,
        width: CONFIG.width,
        height: CONFIG.height,
      }
    );

    const { rect, duration } = stageInfo;

    const validRect =
      Math.abs(rect.x) < 0.01 &&
      Math.abs(rect.y) < 0.01 &&
      Math.abs(rect.width - CONFIG.width) < 0.01 &&
      Math.abs(rect.height - CONFIG.height) < 0.01;

    if (!validRect) {
      throw new Error(
        `Animation stage is not aligned to the viewport:\n` +
        JSON.stringify(rect, null, 2)
      );
    }

    if (!Number.isFinite(duration) || duration <= 0) {
      throw new Error(`Invalid animation duration: ${duration}`);
    }

    const frameCount = Math.ceil(duration * CONFIG.fps);
    const target = page.locator(CONFIG.selector);

    console.log(`Input: ${inputPath}`);
    console.log(`Duration: ${duration}s`);
    console.log(`FPS: ${CONFIG.fps}`);
    console.log(`Frames: ${frameCount}`);
    console.log(
      `Output resolution: ` +
      `${CONFIG.width * CONFIG.deviceScaleFactor}x` +
      `${CONFIG.height * CONFIG.deviceScaleFactor}`
    );
    console.log(`Stage rect: ${JSON.stringify(rect)}`);

    for (let frame = 0; frame < frameCount; frame++) {
      const time = Math.min(duration, frame / CONFIG.fps);

      await page.evaluate(
        ({ selector, time }) => {
          const svg = document.querySelector(selector);

          svg.dispatchEvent(
            new CustomEvent('data-om-seek-to-time-frame', {
              detail: { time },
            })
          );
        },
        {
          selector: CONFIG.selector,
          time,
        }
      );

      // Allow React/SVG rendering to settle after seeking.
      await page.waitForTimeout(20);

      const filename =
        `frame-${String(frame).padStart(4, '0')}.png`;

      await target.screenshot({
        path: path.join(frameDir, filename),
      });

      if (frame % 120 === 0 || frame === frameCount - 1) {
        console.log(`Rendered ${frame + 1}/${frameCount}`);
      }
    }

    console.log(`Frames written to: ${frameDir}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

const { chromium } = require("playwright");
const { spawnSync } = require("child_process");
const { pathToFileURL } = require("url");
const fs = require("fs");
const path = require("path");

const CONFIG = {
  input: "Workforce Runtime Animation (standalone).html",
  output: "workforce-runtime-animation.webp",
  framesDir: ".workforce-runtime-animation-frames",

  width: 1280,
  height: 720,
  deviceScaleFactor: 1.5,

  fps: 30,
  quality: 92,

  selector:
    "svg[data-om-exportable-video-with-duration-secs]",
};

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function normalizeExportLayout(page) {
  return page.evaluate(
    ({ selector, width, height }) => {
      const svg = document.querySelector(selector);

      if (!svg) {
        return null;
      }

      const canvasWrapper = svg.parentElement;
      const stage = canvasWrapper?.parentElement;

      if (!canvasWrapper || !stage) {
        throw new Error(
          "Could not identify animation stage structure"
        );
      }

      function important(element, property, value) {
        element.style.setProperty(
          property,
          value,
          "important"
        );
      }

      const html = document.documentElement;
      const body = document.body;

      for (const element of [html, body]) {
        important(element, "position", "fixed");
        important(element, "inset", "0");
        important(element, "width", `${width}px`);
        important(element, "height", `${height}px`);
        important(element, "min-width", `${width}px`);
        important(element, "min-height", `${height}px`);
        important(element, "max-width", `${width}px`);
        important(element, "max-height", `${height}px`);
        important(element, "margin", "0");
        important(element, "padding", "0");
        important(element, "overflow", "hidden");
        important(element, "background", "#f4f3f0");
      }

      important(stage, "position", "fixed");
      important(stage, "inset", "0");
      important(stage, "display", "block");
      important(stage, "width", `${width}px`);
      important(stage, "height", `${height}px`);
      important(stage, "min-width", `${width}px`);
      important(stage, "min-height", `${height}px`);
      important(stage, "max-width", `${width}px`);
      important(stage, "max-height", `${height}px`);
      important(stage, "margin", "0");
      important(stage, "padding", "0");
      important(stage, "overflow", "hidden");
      important(stage, "background", "#f4f3f0");

      important(canvasWrapper, "position", "absolute");
      important(canvasWrapper, "top", "0");
      important(canvasWrapper, "left", "0");
      important(canvasWrapper, "display", "block");
      important(canvasWrapper, "flex", "none");
      important(canvasWrapper, "width", `${width}px`);
      important(canvasWrapper, "height", `${height}px`);
      important(
        canvasWrapper,
        "min-width",
        `${width}px`
      );
      important(
        canvasWrapper,
        "min-height",
        `${height}px`
      );
      important(
        canvasWrapper,
        "max-width",
        `${width}px`
      );
      important(
        canvasWrapper,
        "max-height",
        `${height}px`
      );
      important(canvasWrapper, "margin", "0");
      important(canvasWrapper, "padding", "0");
      important(canvasWrapper, "overflow", "hidden");

      /*
       * Hide playback controls and every sibling outside the
       * actual SVG canvas.
       */
      for (const child of stage.children) {
        if (child !== canvasWrapper) {
          important(child, "display", "none");
          important(child, "visibility", "hidden");
          important(child, "width", "0");
          important(child, "height", "0");
          important(child, "margin", "0");
          important(child, "padding", "0");
        }
      }

      important(svg, "position", "absolute");
      important(svg, "top", "0");
      important(svg, "left", "0");
      important(svg, "right", "auto");
      important(svg, "bottom", "auto");
      important(svg, "display", "block");
      important(svg, "flex", "none");

      important(svg, "width", `${width}px`);
      important(svg, "height", `${height}px`);
      important(svg, "min-width", `${width}px`);
      important(svg, "min-height", `${height}px`);
      important(svg, "max-width", `${width}px`);
      important(svg, "max-height", `${height}px`);

      important(svg, "margin", "0");
      important(svg, "padding", "0");

      important(svg, "transform", "none");
      important(svg, "transform-origin", "0 0");
      important(svg, "translate", "none");
      important(svg, "scale", "none");

      important(svg, "border", "0");
      important(svg, "outline", "0");
      important(svg, "box-shadow", "none");
      important(svg, "overflow", "hidden");

      const rect = svg.getBoundingClientRect();
      const style = getComputedStyle(svg);

      return {
        duration: Number(
          svg.getAttribute(
            "data-om-exportable-video-with-duration-secs"
          )
        ),
        rect: {
          x: rect.x,
          y: rect.y,
          width: rect.width,
          height: rect.height,
        },
        transform: style.transform,
      };
    },
    {
      selector: CONFIG.selector,
      width: CONFIG.width,
      height: CONFIG.height,
    }
  );
}

function validateLayout(info) {
  if (!info) {
    throw new Error("Animation SVG disappeared");
  }

  const tolerance = 0.05;

  const valid =
    Math.abs(info.rect.x) <= tolerance &&
    Math.abs(info.rect.y) <= tolerance &&
    Math.abs(
      info.rect.width - CONFIG.width
    ) <= tolerance &&
    Math.abs(
      info.rect.height - CONFIG.height
    ) <= tolerance &&
    (
      info.transform === "none" ||
      info.transform ===
        "matrix(1, 0, 0, 1, 0, 0)"
    );

  if (!valid) {
    throw new Error(
      "Invalid export layout:\n" +
        JSON.stringify(info, null, 2)
    );
  }
}

async function waitForAnimation(page) {
  const deadline = Date.now() + 120000;

  while (Date.now() < deadline) {
    const state = await page.evaluate(selector => {
      const svg = document.querySelector(selector);
      const loading =
        document.getElementById("__bundler_loading");
      const error =
        document.getElementById("__bundler_err");

      return {
        hasSvg: Boolean(svg),
        loading: loading?.textContent || "",
        error: error?.textContent || "",
        title: document.title,
        bodyText:
          document.body?.innerText?.slice(0, 1000) || "",
      };
    }, CONFIG.selector);

    if (state.hasSvg) {
      return;
    }

    if (state.error) {
      throw new Error(
        `Bundler error:\n${state.error}`
      );
    }

    await sleep(250);
  }

  const diagnostics = await page.evaluate(selector => {
    return {
      url: location.href,
      title: document.title,
      hasSelector: Boolean(
        document.querySelector(selector)
      ),
      loading:
        document.getElementById("__bundler_loading")
          ?.textContent || "",
      error:
        document.getElementById("__bundler_err")
          ?.textContent || "",
      scripts: Array.from(document.scripts).map(script => ({
        type: script.type,
        src: script.src,
      })),
      html: document.documentElement.outerHTML.slice(
        0,
        5000
      ),
    };
  }, CONFIG.selector);

  await page.screenshot({
    path: "workforce-export-failure.png",
    fullPage: true,
  });

  fs.writeFileSync(
    "workforce-export-diagnostics.json",
    JSON.stringify(diagnostics, null, 2)
  );

  throw new Error(
    "Animation did not render. Diagnostics written to:\n" +
      "workforce-export-diagnostics.json\n" +
      "workforce-export-failure.png"
  );
}

async function main() {
  const inputPath = path.resolve(CONFIG.input);
  const outputPath = path.resolve(CONFIG.output);
  const framesDir = path.resolve(CONFIG.framesDir);

  if (!fs.existsSync(inputPath)) {
    throw new Error(
      `Input HTML does not exist: ${inputPath}`
    );
  }

  fs.rmSync(framesDir, {
    recursive: true,
    force: true,
  });

  fs.mkdirSync(framesDir, {
    recursive: true,
  });

  const browser = await chromium.launch({
    headless: true,
  });

  try {
    const page = await browser.newPage({
      viewport: {
        width: CONFIG.width,
        height: CONFIG.height,
      },
      deviceScaleFactor:
        CONFIG.deviceScaleFactor,
    });

    page.on("console", message => {
      console.log(
        `[browser:${message.type()}]`,
        message.text()
      );
    });

    page.on("pageerror", error => {
      console.error(
        "[pageerror]",
        error.stack || error.message
      );
    });

    console.log(`Opening: ${inputPath}`);

    await page.goto(
      pathToFileURL(inputPath).href,
      {
        waitUntil: "domcontentloaded",
        timeout: 120000,
      }
    );

    await waitForAnimation(page);

    /*
     * Normalize twice because React can update its inline styles
     * immediately after the first rendered frame.
     */
    await normalizeExportLayout(page);
    await page.waitForTimeout(50);

    const initialInfo =
      await normalizeExportLayout(page);

    validateLayout(initialInfo);

    if (
      !Number.isFinite(initialInfo.duration) ||
      initialInfo.duration <= 0
    ) {
      throw new Error(
        `Invalid duration: ${initialInfo.duration}`
      );
    }

    const frameCount = Math.ceil(
      initialInfo.duration * CONFIG.fps
    );

    console.log(
      `Duration: ${initialInfo.duration}s`
    );
    console.log(`FPS: ${CONFIG.fps}`);
    console.log(`Frames: ${frameCount}`);
    console.log(
      `Resolution: ${
        CONFIG.width *
        CONFIG.deviceScaleFactor
      }x${
        CONFIG.height *
        CONFIG.deviceScaleFactor
      }`
    );

    for (
      let frame = 0;
      frame < frameCount;
      frame++
    ) {
      const time = Math.min(
        initialInfo.duration,
        frame / CONFIG.fps
      );

      await page.evaluate(
        ({ selector, time }) => {
          const svg =
            document.querySelector(selector);

          if (!svg) {
            throw new Error(
              "Animation SVG disappeared"
            );
          }

          svg.dispatchEvent(
            new CustomEvent(
              "data-om-seek-to-time-frame",
              {
                detail: { time },
              }
            )
          );
        },
        {
          selector: CONFIG.selector,
          time,
        }
      );

      /*
       * Let React render the requested frame.
       */
      await page.waitForTimeout(25);

      /*
       * Reapply export geometry after React has rewritten its
       * transform and flex layout.
       */
      const currentInfo =
        await normalizeExportLayout(page);

      validateLayout(currentInfo);

      const filename =
        `frame-${String(frame).padStart(
          5,
          "0"
        )}.png`;

      await page.screenshot({
        path: path.join(
          framesDir,
          filename
        ),
        type: "png",
        clip: {
          x: 0,
          y: 0,
          width: CONFIG.width,
          height: CONFIG.height,
        },
      });

      if (
        frame % 60 === 0 ||
        frame === frameCount - 1
      ) {
        console.log(
          `Rendered ${frame + 1}/${frameCount}`
        );
      }
    }
  } finally {
    await browser.close();
  }

  console.log("Encoding animated WebP...");

  const ffmpeg = spawnSync(
    "ffmpeg",
    [
      "-hide_banner",
      "-loglevel",
      "error",
      "-y",

      "-framerate",
      String(CONFIG.fps),

      "-i",
      path.join(
        framesDir,
        "frame-%05d.png"
      ),

      "-loop",
      "0",

      "-c:v",
      "libwebp",

      "-lossless",
      "0",

      "-quality",
      String(CONFIG.quality),

      "-compression_level",
      "6",

      "-preset",
      "picture",

      "-an",

      outputPath,
    ],
    {
      stdio: "inherit",
    }
  );

  if (ffmpeg.error) {
    throw ffmpeg.error;
  }

  if (ffmpeg.status !== 0) {
    throw new Error(
      `FFmpeg exited with code ${ffmpeg.status}`
    );
  }

  const stats = fs.statSync(outputPath);

  console.log("");
  console.log(`Export complete: ${outputPath}`);
  console.log(
    `Size: ${(
      stats.size /
      1024 /
      1024
    ).toFixed(2)} MB`
  );

  fs.rmSync(framesDir, {
    recursive: true,
    force: true,
  });
}

main().catch(error => {
  console.error("");
  console.error("Export failed:");
  console.error(
    error.stack || error.message
  );
  process.exitCode = 1;
});

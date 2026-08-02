import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const stlLoader = new STLLoader();

function createScene(container) {
  const width = container.clientWidth;
  const height = container.clientHeight;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, width / height, 0.1, 1000);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(width, height);
  container.appendChild(renderer.domElement);

  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const key = new THREE.DirectionalLight(0xffffff, 1.1);
  key.position.set(1, 1.4, 1.2);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.5);
  fill.position.set(-1.2, -0.4, -1);
  scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, 0.35);
  rim.position.set(0, -1, -1.5);
  scene.add(rim);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 1.4;
  controls.enablePan = true;
  controls.screenSpacePanning = true;

  let resumeTimer = null;
  controls.addEventListener('start', () => {
    controls.autoRotate = false;
    if (resumeTimer) clearTimeout(resumeTimer);
  });
  controls.addEventListener('end', () => {
    if (resumeTimer) clearTimeout(resumeTimer);
    resumeTimer = setTimeout(() => { controls.autoRotate = true; }, 2500);
  });

  renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
  });

  window.addEventListener('resize', () => {
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (!w || !h) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  });

  let currentMesh = null;

  function showError() {
    container.classList.add('loaded');
    const msg = document.createElement('div');
    msg.textContent = 'model failed to load';
    msg.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:0.78rem;color:var(--text-muted);text-align:center;padding:1rem;';
    container.appendChild(msg);
  }

  function loadSTL(src, onDone) {
    container.classList.remove('loaded');
    const existingMsg = container.querySelector('.viewer-error');
    if (existingMsg) existingMsg.remove();

    stlLoader.load(src, (geometry) => {
      if (currentMesh) {
        scene.remove(currentMesh);
        currentMesh.geometry.dispose();
        currentMesh.material.dispose();
        currentMesh = null;
      }

      geometry.computeBoundingBox();
      geometry.computeVertexNormals();
      const box = geometry.boundingBox;
      const center = new THREE.Vector3();
      box.getCenter(center);
      geometry.translate(-center.x, -center.y, -center.z);

      const size = new THREE.Vector3();
      box.getSize(size);
      const radius = size.length() / 2 || 1;

      const material = new THREE.MeshStandardMaterial({
        color: 0xc9cbd2,
        metalness: 0.15,
        roughness: 0.55,
      });
      const mesh = new THREE.Mesh(geometry, material);
      // These STLs are Z-up (OpenSCAD/CAD/slicer convention: Z is "up,"
      // matching the printer's build direction). three.js is Y-up. A
      // clean -90 deg rotation about X is the correct, general conversion
      // for any part; the previous -Math.PI/2.4 + a Math.PI/6 Z twist were
      // eyeballed against a single part and don't generalize across the 5
      // differently-shaped parts sharing this one viewer.
      mesh.rotation.x = -Math.PI / 2;
      scene.add(mesh);
      currentMesh = mesh;

      camera.position.set(radius * 1.6, radius * 1.2, radius * 1.8);
      camera.near = radius / 100;
      camera.far = radius * 20;
      camera.updateProjectionMatrix();
      controls.target.set(0, 0, 0);
      controls.minDistance = radius * 0.6;
      controls.maxDistance = radius * 6;
      controls.update();

      container.classList.add('loaded');
      if (onDone) onDone();
    }, undefined, (err) => {
      console.error('STL load failed:', src, err);
      showError();
    });
  }

  return { loadSTL };
}

// Simple static viewers: one STL per container, loads once.
document.querySelectorAll('.stl-viewer[data-src]').forEach((container) => {
  const { loadSTL } = createScene(container);
  loadSTL(container.dataset.src);
});

// Tabbed stage viewers: one large viewer with a thumbnail strip to swap models.
document.querySelectorAll('.part-viewer').forEach((widget) => {
  const stage = widget.querySelector('.stl-viewer-stage');
  const tabs = widget.querySelectorAll('.part-tab');
  const nameEl = widget.querySelector('.part-active-name');
  const descEl = widget.querySelector('.part-active-desc');
  const downloadEl = widget.querySelector('.part-active-download');
  if (!stage || !tabs.length) return;

  const { loadSTL } = createScene(stage);

  function activate(tab) {
    tabs.forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');
    loadSTL(tab.dataset.src);
    if (nameEl) nameEl.textContent = tab.dataset.name || '';
    if (descEl) descEl.textContent = tab.dataset.desc || '';
    if (downloadEl) {
      downloadEl.href = tab.dataset.src;
      downloadEl.textContent = `Download ${tab.dataset.name || 'model'} (.stl)`;
    }
  }

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => activate(tab));
  });

  activate(tabs[0]);
});

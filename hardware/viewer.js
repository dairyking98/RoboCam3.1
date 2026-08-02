import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js';
import { STLLoader } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/loaders/STLLoader.js';
import { OrbitControls } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/OrbitControls.js';

function initViewer(container) {
  const src = container.dataset.src;
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

  const material = new THREE.MeshStandardMaterial({
    color: 0xc9cbd2,
    metalness: 0.15,
    roughness: 0.55,
  });

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

  const loader = new STLLoader();
  loader.load(src, (geometry) => {
    geometry.computeBoundingBox();
    geometry.computeVertexNormals();
    const box = geometry.boundingBox;
    const center = new THREE.Vector3();
    box.getCenter(center);
    geometry.translate(-center.x, -center.y, -center.z);

    const size = new THREE.Vector3();
    box.getSize(size);
    const radius = size.length() / 2 || 1;

    const mesh = new THREE.Mesh(geometry, material);
    mesh.rotation.x = -Math.PI / 2.4;
    mesh.rotation.z = Math.PI / 6;
    scene.add(mesh);

    camera.position.set(radius * 1.6, radius * 1.2, radius * 1.8);
    camera.near = radius / 100;
    camera.far = radius * 20;
    camera.updateProjectionMatrix();
    controls.target.set(0, 0, 0);
    controls.minDistance = radius * 0.6;
    controls.maxDistance = radius * 6;
    controls.update();

    container.classList.add('loaded');
  }, undefined, (err) => {
    console.error('STL load failed:', src, err);
    container.classList.add('loaded');
    container.style.setProperty('--fallback', '1');
    const msg = document.createElement('div');
    msg.textContent = 'model failed to load';
    msg.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:0.78rem;color:var(--text-muted);text-align:center;padding:1rem;';
    container.appendChild(msg);
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
}

document.querySelectorAll('.stl-viewer').forEach(initViewer);

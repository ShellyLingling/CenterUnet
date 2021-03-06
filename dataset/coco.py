from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import json
import math
import cv2
import numpy as np

import torch.utils.data as data
import torch

from utils.image import flip, color_aug
from utils.image import get_affine_transform, affine_transform
from utils.image import gaussian_radius, draw_umich_gaussian, draw_elipse_gaussian, draw_msra_gaussian
from utils.image import draw_dense_reg
from utils.image import size2level, levelnum

import pycocotools.coco as coco
from pycocotools.cocoeval import COCOeval

class COCO(data.Dataset):
  class_name = [
    '__background__', 'person', 'bicycle', 'car', 'motorcycle', 'airplane',
    'bus', 'train', 'truck', 'boat', 'traffic light', 'fire hydrant',
    'stop sign', 'parking meter', 'bench', 'bird', 'cat', 'dog', 'horse',
    'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe', 'backpack',
    'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee', 'skis',
    'snowboard', 'sports ball', 'kite', 'baseball bat', 'baseball glove',
    'skateboard', 'surfboard', 'tennis racket', 'bottle', 'wine glass',
    'cup', 'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple', 'sandwich',
    'orange', 'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake',
    'chair', 'couch', 'potted plant', 'bed', 'dining table', 'toilet', 'tv',
    'laptop', 'mouse', 'remote', 'keyboard', 'cell phone', 'microwave',
    'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock', 'vase',
    'scissors', 'teddy bear', 'hair drier', 'toothbrush']

  all_valid_ids = [
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13,
    14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
    24, 25, 27, 28, 31, 32, 33, 34, 35, 36,
    37, 38, 39, 40, 41, 42, 43, 44, 46, 47,
    48, 49, 50, 51, 52, 53, 54, 55, 56, 57,
    58, 59, 60, 61, 62, 63, 64, 65, 67, 70,
    72, 73, 74, 75, 76, 77, 78, 79, 80, 81,
    82, 84, 85, 86, 87, 88, 89, 90]

  '''
  _valid_ids = all_valid_ids
  '''
  _valid_ids = [
    all_valid_ids[class_name.index('person')-1],
    all_valid_ids[class_name.index('bird')-1],
    all_valid_ids[class_name.index('cat')-1],
    all_valid_ids[class_name.index('dog')-1],
    all_valid_ids[class_name.index('horse')-1],
    all_valid_ids[class_name.index('sheep')-1],
    all_valid_ids[class_name.index('cow')-1],
    all_valid_ids[class_name.index('elephant')-1],
    all_valid_ids[class_name.index('bear')-1],
    all_valid_ids[class_name.index('zebra')-1],
    all_valid_ids[class_name.index('giraffe')-1]
    ]

  cat_ids = {v: i for i, v in enumerate(_valid_ids)}

  num_classes = len(_valid_ids)

  default_resolution = [512, 512]
  mean = np.array([0.40789654, 0.44719302, 0.47026115],
                   dtype=np.float32).reshape(1, 1, 3)
  std = np.array([0.28863828, 0.27408164, 0.27809835],
                   dtype=np.float32).reshape(1, 1, 3)

  _data_rng = np.random.RandomState(123)
  _eig_val = np.array([0.2141788, 0.01817699, 0.00341571],
                           dtype=np.float32)
  _eig_vec = np.array([
      [-0.58752847, -0.69563484, 0.41340352],
      [-0.5832747, 0.00994535, -0.81221408],
      [-0.56089297, 0.71832671, 0.41158938]
  ], dtype=np.float32)

  def __init__(self, split, opt):
    super(COCO, self).__init__()

    self.data_dir = os.path.join(opt.data_dir, 'coco')

    # self.img_dir = os.path.join(self.data_dir, '{}2017'.format(split))
    # self.annot_path = os.path.join(
    #     self.data_dir, 'annotations', 'instances_{}2017.json'
    #     ).format(split)

    self.img_dir = os.path.join(self.data_dir, '{}2017'.format(split))
    self.annot_path = os.path.join(
        self.data_dir, 'annotations',
        'instances_{}2017.json').format(split)

    self.max_objs = 128
    self.split = split
    self.opt = opt

    self.patch_sizes = [opt.input_w]

    sizes = [128, 192, 256, 320, 384, 448, 512, 640, 768, 896, 1024, 1280, 1536, 2048]

    for i, size in enumerate(sizes[2:]):
         if opt.input_w < sizes[i-2]:
             break

         self.patch_sizes.append(size)

    self.getcount = 0

    print('==> initializing coco 2017 {} data.'.format(split))
    self.coco = coco.COCO(self.annot_path)
    self.images = self.coco.getImgIds()
    self.num_samples = len(self.images)

    print('Loaded {} {} samples'.format(split, self.num_samples))

    images = []

    for i in range(0, self.num_samples):
        img_id = self.images[i]
        ann_ids = self.coco.getAnnIds(imgIds=[img_id])
        anns = self.coco.loadAnns(ids=ann_ids)
        num_objs = min(len(anns), 128)

        objflag = False

        for j in range(0, num_objs):
            ann = anns[j]
            bbox = self._coco_box_to_bbox(ann['bbox'])

            if ann['category_id'] not in self._valid_ids:
              continue

            x1 = int(bbox[0])
            y1 = int(bbox[1])
            x2 = int(bbox[2])
            y2 = int(bbox[3])

            h, w = bbox[3] - bbox[1], bbox[2] - bbox[0]

            if h*w >= 50*50:
              objflag = True
              break

        if objflag:
          images.append(img_id)

    self.images = images
    self.num_samples = len(self.images)

    print('Loaded {} {} samples'.format(split, self.num_samples))

  def __len__(self):
    return self.num_samples

  def _coco_box_to_bbox(self, box):
    bbox = np.array([box[0], box[1], box[0] + box[2], box[1] + box[3]],
                    dtype=np.float32)
    return bbox

  def _get_border(self, border, size):
    i = 1
    while size - border // i <= border // i:
        i *= 2
    return border // i

  def assignroi(self, pagenum, dst, src, x1, y1, x2, y2):
    dst[y1:y2, x1:x2, pagenum] = np.bitwise_or(dst[y1:y2, x1:x2, pagenum], src[y1:y2, x1:x2])

  def __getitem__(self, index):
    img_id = self.images[index]
    file_name = self.coco.loadImgs(ids=[img_id])[0]['file_name']
    img_path = os.path.join(self.img_dir, file_name)
    ann_ids = self.coco.getAnnIds(imgIds=[img_id])
    anns = self.coco.loadAnns(ids=ann_ids)
    num_objs = min(len(anns), self.max_objs)

    img = cv2.imread(img_path)

    height, width = img.shape[0], img.shape[1]
    c = np.array([img.shape[1] / 2., img.shape[0] / 2.], dtype=np.float32)

    if self.opt.keep_res:
      input_h = (height | self.opt.pad) + 1
      input_w = (width | self.opt.pad) + 1
      s = np.array([input_w, input_h], dtype=np.float32)
    else:
      s = max(img.shape[0], img.shape[1]) * 1.0
      input_h, input_w = self.opt.input_h, self.opt.input_w

      if self.split == 'train':
          input_w = self.patch_sizes[(self.getcount//self.opt.batch_size) % len(self.patch_sizes)]
          input_h = input_w

          self.getcount = 0 if self.getcount == self.num_samples else self.getcount + 1

    flipped = False

    if self.split == 'train':
      if not self.opt.not_rand_crop:
        s = s * np.random.choice(np.arange(0.6, 1.4, 0.1))

        w_border = self._get_border(128, img.shape[1])
        h_border = self._get_border(128, img.shape[0])

        c[0] = np.random.randint(low=w_border, high=img.shape[1] - w_border)
        c[1] = np.random.randint(low=h_border, high=img.shape[0] - h_border)
      else:
        sf = self.opt.scale
        cf = self.opt.shift

        c[0] += s * np.clip(np.random.randn()*cf, -2*cf, 2*cf)
        c[1] += s * np.clip(np.random.randn()*cf, -2*cf, 2*cf)

        s = s * np.clip(np.random.randn()*sf + 1, 1 - sf, 1 + sf)

      if np.random.random() < self.opt.flip:
        flipped = True
        img = img[:, ::-1, :]
        c[0] =  width - c[0] - 1


    trans_input = get_affine_transform(c, s, 0, [input_w, input_h])
    inp = cv2.warpAffine(img, trans_input, (input_w, input_h), flags=cv2.INTER_LINEAR)
    inp = (inp.astype(np.float32) / 255.)

    if self.split == 'train' and not self.opt.no_color_aug:
      color_aug(self._data_rng, inp, self._eig_val, self._eig_vec)

    inp = (inp - self.mean) / self.std
    inp = inp.transpose(2, 0, 1)

    output_h = input_h // self.opt.down_ratio
    output_w = input_w // self.opt.down_ratio
    num_classes = self.num_classes

    trans_output = get_affine_transform(c, s, 0, [output_w, output_h])

    hm = np.zeros((num_classes, output_h, output_w), dtype=np.float32)
    wh = np.zeros((self.max_objs, 2), dtype=np.float32)
    dense_wh = np.zeros((2, output_h, output_w), dtype=np.float32)
    reg = np.zeros((self.max_objs, 2), dtype=np.float32)
    ind = np.zeros((self.max_objs), dtype=np.int64)
    reg_mask = np.zeros((self.max_objs), dtype=np.uint8)
    cat_spec_wh = np.zeros((self.max_objs, num_classes * 2), dtype=np.float32)
    cat_spec_mask = np.zeros((self.max_objs, num_classes * 2), dtype=np.uint8)

    draw_gaussian = draw_msra_gaussian if self.opt.mse_loss else \
                    draw_umich_gaussian

    gt_det = []

    allmask = np.zeros((output_h, output_w, self.opt.num_maskclasses+levelnum), dtype=np.uint8)

    for k in range(num_objs):
      ann = anns[k]
      bbox = self._coco_box_to_bbox(ann['bbox'])

      if ann['category_id'] not in self._valid_ids:
        continue

      cls_id = int(self.cat_ids[ann['category_id']])

      if flipped:
        bbox[[0, 2]] = width - bbox[[2, 0]] - 1

      bbox[:2] = affine_transform(bbox[:2], trans_output)
      bbox[2:] = affine_transform(bbox[2:], trans_output)
      bbox[[0, 2]] = np.clip(bbox[[0, 2]], 0, output_w - 1)
      bbox[[1, 3]] = np.clip(bbox[[1, 3]], 0, output_h - 1)

      x1 = int(bbox[0])
      y1 = int(bbox[1])
      x2 = int(bbox[2])
      y2 = int(bbox[3])

      h, w = bbox[3] - bbox[1], bbox[2] - bbox[0]

      if h > 0 and w > 0:
        ### gen mask begin ###
        # clsbase = cls_id*9
        clsbase = 0*9
        mask = self.coco.annToMask(ann)

        if flipped:
          mask = mask[:, ::-1]

        mask = cv2.warpAffine(mask, trans_output, (output_w, output_h), flags=cv2.INTER_LINEAR)

        roi = mask[y1:y2, x1:x2]
        roi_h, roi_w = roi.shape

        if roi_h < 6 or roi_w < 6:
          continue

        l = size2level(output_w*output_h, roi_w*roi_h)
        allmask[:,:,self.opt.num_maskclasses+l] = np.bitwise_or(allmask[:,:,self.opt.num_maskclasses+l], mask)
        allmask[:,:,self.opt.num_maskclasses+l+1] = np.bitwise_or(allmask[:,:,self.opt.num_maskclasses+l+1], mask)

        roi_cx = roi_w//2
        roi_cy = roi_h//2
        cell_w = (roi_w+5)//6
        cell_h = (roi_h+5)//6

        allmaskroi = allmask[y1:y2, x1:x2, :]

        ww = max(6,cell_w//4)
        hh = max(6,cell_h//4)

        # TOP
        self.assignroi(0, allmaskroi, roi, 0,                0,                roi_cx-cell_w+ww, roi_cy-cell_h+hh)
        self.assignroi(1, allmaskroi, roi, roi_cx-cell_w-ww, 0,                roi_cx+cell_w+ww, roi_cy-cell_h+hh)
        self.assignroi(2, allmaskroi, roi, roi_cx+cell_w-ww, 0,                roi_w,            roi_cy-cell_h+hh)

        # MIDDLE
        self.assignroi(3, allmaskroi, roi, 0,                roi_cy-cell_h-hh, roi_cx-cell_w+ww, roi_cy+cell_h+hh)
        self.assignroi(4, allmaskroi, roi, roi_cx-cell_w-ww, roi_cy-cell_h-hh, roi_cx+cell_w+ww, roi_cy+cell_h+hh)
        self.assignroi(5, allmaskroi, roi, roi_cx+cell_w-ww, roi_cy-cell_h-hh, roi_w,            roi_cy+cell_h+hh)

        # BOTTOM
        self.assignroi(6, allmaskroi, roi, 0,                roi_cy+cell_h-hh, roi_cx-cell_w+ww, roi_h           )
        self.assignroi(7, allmaskroi, roi, roi_cx-cell_w-ww, roi_cy+cell_h-hh, roi_cx+cell_w+ww, roi_h           )
        self.assignroi(8, allmaskroi, roi, roi_cx+cell_w-ww, roi_cy+cell_h-hh, roi_w,            roi_h           )
        ### gen mask end ###

        radius = gaussian_radius((math.ceil(h), math.ceil(w)))
        radius = max(0, int(radius))

        ct = np.array([(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2], dtype=np.float32)
        ct_int = ct.astype(np.int32)

        if self.opt.mse_loss:
          radius = self.opt.hm_gauss
          draw_gaussian(hm[cls_id], ct_int, radius)
        else:
          #draw_gaussian(hm[cls_id], ct_int, radius)
          xradius = int(gaussian_radius((math.ceil(w),math.ceil(w))))
          yradius = int(gaussian_radius((math.ceil(h),math.ceil(h))))
          draw_elipse_gaussian(hm[cls_id], ct_int, (xradius,yradius))

        wh[k] = 1. * w, 1. * h
        ind[k] = ct_int[1] * output_w + ct_int[0]
        reg[k] = ct - ct_int
        reg_mask[k] = 1

        cat_spec_wh[k, cls_id * 2: cls_id * 2 + 2] = wh[k]
        cat_spec_mask[k, cls_id * 2: cls_id * 2 + 2] = 1

        if self.opt.dense_wh:
          draw_dense_reg(dense_wh, hm.max(axis=0), ct_int, wh[k], radius)

        gt_det.append([ct[0] - w / 2, ct[1] - h / 2, ct[0] + w / 2, ct[1] + h / 2, 1, cls_id])

    #cv2.imwrite("./results/hehe.jpg", (hm.max(axis=0).squeeze()*255).astype(np.uint8))

    if index % 30 == 0:
      cv2.imwrite("./results/top.jpg", (allmask[:,:,0:3]*255).astype(np.uint8))
      cv2.imwrite("./results/middle.jpg", (allmask[:,:,3:6]*255).astype(np.uint8))
      cv2.imwrite("./results/bottom.jpg", (allmask[:,:,6:9]*255).astype(np.uint8))
      cv2.imwrite("./results/full.jpg", (((allmask[:,:,0:3]+allmask[:,:,3:6]+allmask[:,:,6:9]) > 0)*255).astype(np.uint8))
      cv2.imwrite("./results/large.jpg", (((allmask[:,:,9:12]) > 0)*255).astype(np.uint8))
      cv2.imwrite("./results/small.jpg", (((allmask[:,:,12:15]) > 0)*255).astype(np.uint8))

    ret = {
      'input': inp, 'hm': hm, 'reg_mask': reg_mask, 'ind': ind, 'wh': wh,
      'allmask': allmask.astype(np.float32).transpose(2, 0, 1)
    }

    if self.opt.dense_wh:
      hm_a = hm.max(axis=0, keepdims=True)
      dense_wh_mask = np.concatenate([hm_a, hm_a], axis=0)
      ret.update({'dense_wh': dense_wh, 'dense_wh_mask': dense_wh_mask})
      del ret['wh']
    elif self.opt.cat_spec_wh:
      ret.update({'cat_spec_wh': cat_spec_wh, 'cat_spec_mask': cat_spec_mask})
      del ret['wh']

    if self.opt.reg_offset:
      ret.update({'reg': reg})

    #if self.opt.debug > 0 or not self.split == 'train':
    if not self.split == 'train':
      if len(gt_det) > 0:
        gt_det = np.array(gt_det, dtype=np.float32)
      else:
        gt_det = np.zeros((1, 6), dtype=np.float32)

      meta = {'c': c, 's': s, 'gt_det': gt_det, 'img_id': img_id}
      ret['meta'] = meta

    # img = cv2.warpAffine(img, trans_output, (output_w, output_h), flags=cv2.INTER_LINEAR)
    # img = img*allmask[:,:,:3]
    # cv2.imwrite("./results/maskit.jpg", img)

    return ret

  def verbose(self, drawit=True):
    draw_gaussian = draw_msra_gaussian if self.opt.mse_loss else \
                    draw_umich_gaussian

    classnums = {}

    for i in range(0, self.num_samples):
        img_id = self.images[i]
        ann_ids = self.coco.getAnnIds(imgIds=[img_id])
        anns = self.coco.loadAnns(ids=ann_ids)
        num_objs = min(len(anns), 128)

        if drawit:
            file_name = self.coco.loadImgs(ids=[img_id])[0]['file_name']
            img_path = os.path.join(self.img_dir, file_name)
            img = cv2.imread(img_path)
            height, width = img.shape[0], img.shape[1]

            hm = np.zeros((self.num_classes, height, width), dtype=np.float32)

        for j in range(0, num_objs):
            ann = anns[j]
            bbox = self._coco_box_to_bbox(ann['bbox'])

            if ann['category_id'] not in self._valid_ids:
              continue

            cls_id = int(self.cat_ids[ann['category_id']])

            h, w = bbox[3] - bbox[1], bbox[2] - bbox[0]

            if drawit:
              if h > 0 and w > 0:
                radius = gaussian_radius((math.ceil(h), math.ceil(w)))
                radius = max(0, int(radius))

                ct = np.array([(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2], dtype=np.float32)
                ct_int = ct.astype(np.int32)

                if self.opt.mse_loss:
                  radius = self.opt.hm_gauss
                  draw_gaussian(hm[cls_id], ct_int, radius)
                else:
                  #draw_gaussian(hm[cls_id], ct_int, radius)
                  xradius = int(gaussian_radius((math.ceil(w),math.ceil(w))))
                  yradius = int(gaussian_radius((math.ceil(h),math.ceil(h))))
                  draw_elipse_gaussian(hm[cls_id], ct_int, (xradius,yradius))

            x1 = bbox[0]
            y1 = bbox[1]
            x2 = bbox[2]
            y2 = bbox[3]

            name = self.class_name[cls_id+1]

            if name in classnums.keys():
                classnums[name] += 1
            else:
                classnums[name] = 0

            if drawit:
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(img, self.class_name[cls_id+1], (x1, y1), cv2.FONT_HERSHEY_SIMPLEX, 1, ((cls_id+1)*2, 255, 255-(cls_id+1)), 2)

        if drawit:
            middlename = "train2017"

            resultfile = img_path.replace(middlename, "bboximages")

            if os.path.exists(os.path.dirname(resultfile)) and not os.path.exists(resultfile):
              cv2.imwrite(resultfile, img)

              hm = (np.amax(hm, axis=0)*255).astype(np.uint8)
              cv2.imwrite(resultfile.replace(".jpg", "_hm.jpg"), hm)

              print(resultfile)

    for name in self.class_name:
        if name in classnums.keys() and name != '__background__':
            print(name, ' '*(32 - len(name)), classnums[name])

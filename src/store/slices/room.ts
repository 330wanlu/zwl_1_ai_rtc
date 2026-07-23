/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { createSlice } from '@reduxjs/toolkit';
import {
  AudioPropertiesInfo,
  LocalAudioStats,
  NetworkQuality,
  RemoteAudioStats,
} from '@volcengine/rtc';
import RtcClient from '@/lib/RtcClient';

export interface IUser {
  username?: string;
  userId?: string;
  publishAudio?: boolean;
  publishVideo?: boolean;
  publishScreen?: boolean;
  audioStats?: RemoteAudioStats;
  audioPropertiesInfo?: AudioPropertiesInfo;
}

export type LocalUser = Omit<IUser, 'audioStats'> & {
  loginToken?: string;
  audioStats?: LocalAudioStats;
};

export interface Msg {
  value: string;
  time: string;
  user: string;
  paragraph?: boolean;
  definite?: boolean;
  isInterrupted?: boolean;
  /** 多模态媒体（数据中台侧通道） */
  media?: MsgMedia[];
  mediaTitle?: string;
}

export interface MsgMedia {
  id: number | string;
  type: string;
  url: string;
  name?: string;
  caption?: string | null;
}

export interface SceneConfig {
  id: string;
  icon?: string;
  name?: string;
  questions?: string[];
  botName: string;
  isVision: boolean;
  isScreenMode: boolean;
  isInterruptMode: boolean;
  isAvatarScene: boolean;
  avatarBgUrl: string;
}

export interface RTCConfig {
  AppId: string;
  RoomId: string;
  UserId: string;
  Token: string;
}

export interface RoomState {
  time: number;
  roomId?: string;
  localUser: LocalUser;
  remoteUsers: IUser[];
  autoPlayFailUser: string[];
  /**
   * @brief 是否已加房
   */
  isJoined: boolean;
  /**
   * @brief 选择的场景
   */
  scene: string;
  /**
   * @brief 场景下的配置
   */
  sceneConfigMap: Record<string, SceneConfig>;
  /**
   * @brief RTC 相关的配置
   */
  rtcConfigMap: Record<string, RTCConfig>;

  /**
   * @brief AI 通话是否启用
   */
  isAIGCEnable: boolean;
  /**
   * @brief AI 是否正在说话
   */
  isAITalking: boolean;
  /**
   * @brief AI 思考中
   */
  isAIThinking: boolean;
  /**
   * @brief 用户是否正在说话
   */
  isUserTalking: boolean;
  /**
   * @brief 网络质量
   */
  networkQuality: NetworkQuality;

  /**
   * @brief 对话记录
   */
  msgHistory: Msg[];

  /**
   * @brief 当前的对话
   */
  currentConversation: {
    [user: string]: {
      /**
       * @brief 实时对话内容
       */
      msg: string;
      /**
       * @brief 当前实时对话内容是否能被定义为 "问题"
       */
      definite: boolean;
    };
  };

  /**
   * @brief 是否显示字幕
   */
  isShowSubtitle: boolean;

  /**
   * @brief 是否全屏
   */
  isFullScreen: boolean;

  /**
   * @brief 自定义人设名称
   */
  customSceneName: string;

  /**
   * @brief 媒体到达时若尚无本轮 AI 气泡，先暂存，等字幕出来再挂上
   */
  pendingMediaAttach: {
    media: MsgMedia[];
    mediaTitle?: string;
    answer?: string;
  } | null;
}

const initialState: RoomState = {
  time: -1,
  scene: '',
  sceneConfigMap: {},
  rtcConfigMap: {},
  remoteUsers: [],
  localUser: {
    publishAudio: false,
    publishVideo: false,
    publishScreen: false,
  },
  autoPlayFailUser: [],
  isJoined: false,
  isAIGCEnable: false,
  isAIThinking: false,
  isAITalking: false,
  isUserTalking: false,
  networkQuality: NetworkQuality.UNKNOWN,

  msgHistory: [],
  currentConversation: {},
  isShowSubtitle: true,
  isFullScreen: false,
  customSceneName: '',
  pendingMediaAttach: null,
};

function isBotMsgUser(state: RoomState, user: string): boolean {
  const botName = state.sceneConfigMap[state.scene]?.botName || '';
  return Boolean(user) && (user === botName || user.includes('voiceChat_'));
}

function mergeMediaOntoMsg(
  msg: Msg,
  media: MsgMedia[],
  mediaTitle?: string
): void {
  const existed = new Set((msg.media || []).map((m) => `${m.type}:${m.url}`));
  const merged = [...(msg.media || [])];
  for (const m of media) {
    const key = `${m.type}:${m.url}`;
    if (!existed.has(key)) {
      merged.push(m);
      existed.add(key);
    }
  }
  msg.media = merged;
  if (mediaTitle) {
    msg.mediaTitle = mediaTitle;
  }
}

/** 最近一次用户消息下标；找不到则为 -1 */
function findLastUserMsgIndex(state: RoomState): number {
  const userId = state.localUser.userId;
  for (let i = state.msgHistory.length - 1; i >= 0; i--) {
    const msg = state.msgHistory[i];
    if (userId && msg.user === userId) {
      return i;
    }
    // 非 bot 也视为用户侧（兼容 userId 尚未写入的情况）
    if (!isBotMsgUser(state, msg.user)) {
      return i;
    }
  }
  return -1;
}

/** 本轮（最近用户提问之后）未打断的 AI 气泡下标 */
function findCurrentTurnBotMsgIndex(state: RoomState): number {
  const lastUserIdx = findLastUserMsgIndex(state);
  for (let i = state.msgHistory.length - 1; i > lastUserIdx; i--) {
    const msg = state.msgHistory[i];
    if (isBotMsgUser(state, msg.user) && !msg.isInterrupted) {
      return i;
    }
  }
  return -1;
}

function flushPendingMediaAttach(state: RoomState): void {
  const pending = state.pendingMediaAttach;
  if (!pending?.media?.length) {
    return;
  }
  const targetIdx = findCurrentTurnBotMsgIndex(state);
  if (targetIdx < 0) {
    return;
  }
  mergeMediaOntoMsg(state.msgHistory[targetIdx], pending.media, pending.mediaTitle);
  state.pendingMediaAttach = null;
}

export const roomSlice = createSlice({
  name: 'room',
  initialState,
  reducers: {
    localJoinRoom: (
      state,
      {
        payload,
      }: {
        payload: {
          roomId: string;
          user: LocalUser;
        };
      }
    ) => {
      state.roomId = payload.roomId;
      state.localUser = {
        ...state.localUser,
        ...payload.user,
      };
      state.isJoined = true;
    },
    localLeaveRoom: (state) => {
      state.roomId = undefined;
      state.time = -1;
      state.localUser = {
        publishAudio: false,
        publishVideo: false,
        publishScreen: false,
      };
      state.remoteUsers = [];
      state.isJoined = false;
      state.pendingMediaAttach = null;
    },
    remoteUserJoin: (state, { payload }) => {
      state.remoteUsers.push(payload);
    },
    remoteUserLeave: (state, { payload }) => {
      const findIndex = state.remoteUsers.findIndex((user) => user.userId === payload.userId);
      state.remoteUsers.splice(findIndex, 1);
    },

    updateScene: (state, { payload }) => {
      state.scene = payload;
    },

    updateSceneConfig: (state, { payload }) => {
      state.sceneConfigMap = payload;
    },

    updateRTCConfig: (state, { payload }) => {
      state.rtcConfigMap = payload;
      RtcClient.basicInfo = {
        app_id: payload[state.scene].AppId,
        room_id: payload[state.scene].RoomId,
        user_id: payload[state.scene].UserId,
        token: payload[state.scene].Token,
      };
    },

    updateLocalUser: (state, { payload }: { payload: Partial<LocalUser> }) => {
      state.localUser = {
        ...state.localUser,
        ...(payload || {}),
      };
    },

    updateNetworkQuality: (state, { payload }) => {
      state.networkQuality = payload.networkQuality;
    },

    updateRemoteUser: (state, { payload }: { payload: IUser | IUser[] }) => {
      if (!Array.isArray(payload)) {
        payload = [payload];
      }

      payload.forEach((user) => {
        const findIndex = state.remoteUsers.findIndex((u) => u.userId === user.userId);
        state.remoteUsers[findIndex] = {
          ...state.remoteUsers[findIndex],
          ...user,
        };
      });
    },

    updateRoomTime: (state, { payload }) => {
      state.time = payload.time;
    },

    addAutoPlayFail: (state, { payload }) => {
      const autoPlayFailUser = state.autoPlayFailUser;
      const index = autoPlayFailUser.findIndex((item) => item === payload.userId);
      if (index === -1) {
        state.autoPlayFailUser.push(payload.userId);
      }
    },
    removeAutoPlayFail: (state, { payload }) => {
      const autoPlayFailUser = state.autoPlayFailUser;
      const _autoPlayFailUser = autoPlayFailUser.filter((item) => item !== payload.userId);
      state.autoPlayFailUser = _autoPlayFailUser;
    },
    clearAutoPlayFail: (state) => {
      state.autoPlayFailUser = [];
    },
    updateAIGCState: (state, { payload }) => {
      state.isAIGCEnable = payload.isAIGCEnable;
    },
    updateAITalkState: (state, { payload }) => {
      state.isAIThinking = false;
      state.isUserTalking = false;
      state.isAITalking = payload.isAITalking;
    },
    updateAIThinkState: (state, { payload }) => {
      state.isAIThinking = payload.isAIThinking;
      state.isUserTalking = false;
    },
    clearHistoryMsg: (state) => {
      state.msgHistory = [];
      state.pendingMediaAttach = null;
    },
    setHistoryMsg: (state, { payload }) => {
      const { paragraph, definite } = payload;
      const lastMsg = state.msgHistory.at(-1)! || {};
      /** 是否需要再创建新句子 */
      const fromBot =
        payload.user === state.sceneConfigMap[state.scene].botName ||
        payload.user.includes('voiceChat_');
      /**
       * Bot 的语句：
       * 1. 在 SubtitleMode=0 时（未启用数字人时默认值），以 definite 判断是否需要追加新内容
       * 2. 在 SubtitleMode=1 时（启用数字人时强制设定为 1），以 paragraph 判断是否需要追加新内容
       * User 的语句以 paragraph 判断是否需要追加新内容
       */
      const currentSubtitleMode = state.sceneConfigMap[state.scene].isAvatarScene ? 1 : 0;
      /** 已打断的气泡视为已结束，后续内容开新气泡，避免媒体/正文挂错行 */
      const lastMsgCompleted =
        Boolean(lastMsg.isInterrupted) ||
        (!fromBot || currentSubtitleMode ? lastMsg.paragraph : lastMsg.definite);

      if (state.msgHistory.length) {
        /** 如果上一句话是完整的则新增语句 */
        if (lastMsgCompleted) {
          state.msgHistory.push({
            value: payload.text,
            time: new Date().toString(),
            user: payload.user,
            definite,
            paragraph,
          });
        } else {
          /** 话未说完, 更新文字内容 */
          if (fromBot && currentSubtitleMode) {
            lastMsg.value += payload.text;
          } else {
            lastMsg.value = payload.text;
          }
          lastMsg.time = new Date().toString();
          lastMsg.paragraph = paragraph;
          lastMsg.definite = definite;
          lastMsg.user = payload.user;
        }
      } else {
        /** 首句话首字不会被打断 */
        state.msgHistory.push({
          value: payload.text,
          time: new Date().toString(),
          user: payload.user,
          paragraph,
        });
      }

      // 本轮 AI 字幕出现后，把暂存的媒体挂上来（不挂到「已打断」旧气泡）
      if (fromBot) {
        flushPendingMediaAttach(state);
      }
    },
    setInterruptMsg: (state) => {
      // 打断：标记旧气泡结束；已在该气泡上的 media 保留。
      // 未拉取的服务端 pending 靠 TTL；前端暂存清空，避免下一轮误挂到旧上下文。
      state.isAITalking = false;
      state.pendingMediaAttach = null;
      if (!state.msgHistory.length) {
        return;
      }
      /** 找到最后一个末尾的字幕, 将其状态置换为打断，并视为已完成句子 */
      for (let id = state.msgHistory.length - 1; id >= 0; id--) {
        const msg = state.msgHistory[id];
        if (msg.value) {
          state.msgHistory[id].isInterrupted = true;
          state.msgHistory[id].definite = true;
          state.msgHistory[id].paragraph = true;
          break;
        }
      }
    },
    clearCurrentMsg: (state) => {
      state.currentConversation = {};
      state.msgHistory = [];
      state.isAITalking = false;
      state.isUserTalking = false;
      state.pendingMediaAttach = null;
    },
    /**
     * 将中台媒体挂到「最近一次用户提问之后」的未打断 AI 气泡；
     * 若本轮 AI 字幕还没到，先暂存，等 setHistoryMsg 再挂。
     */
    attachMediaToLatestAIMsg: (
      state,
      {
        payload,
      }: {
        payload: {
          media: MsgMedia[];
          mediaTitle?: string;
          answer?: string;
        };
      }
    ) => {
      const media = (payload.media || []).filter((m) => m?.url);
      if (!media.length) {
        return;
      }
      const targetIdx = findCurrentTurnBotMsgIndex(state);
      if (targetIdx >= 0) {
        mergeMediaOntoMsg(state.msgHistory[targetIdx], media, payload.mediaTitle);
        state.pendingMediaAttach = null;
        return;
      }
      // 字幕未到：暂存，禁止挂到上一轮（含已打断）气泡
      const existed = state.pendingMediaAttach?.media || [];
      const merged = [...existed];
      const keys = new Set(merged.map((m) => `${m.type}:${m.url}`));
      for (const m of media) {
        const key = `${m.type}:${m.url}`;
        if (!keys.has(key)) {
          merged.push(m);
          keys.add(key);
        }
      }
      state.pendingMediaAttach = {
        media: merged,
        mediaTitle: payload.mediaTitle || state.pendingMediaAttach?.mediaTitle,
        answer: payload.answer || state.pendingMediaAttach?.answer,
      };
    },
    updateShowSubtitle: (state, { payload }) => {
      state.isShowSubtitle = payload.isShowSubtitle;
    },
    updateFullScreen: (state, { payload }) => {
      state.isFullScreen = payload.isFullScreen;
    },
    updatecustomSceneName: (state, { payload }) => {
      state.customSceneName = payload.customSceneName;
    },
  },
});

export const {
  localJoinRoom,
  localLeaveRoom,
  remoteUserJoin,
  remoteUserLeave,
  updateRemoteUser,
  updateLocalUser,
  updateRoomTime,
  addAutoPlayFail,
  removeAutoPlayFail,
  clearAutoPlayFail,
  updateAIGCState,
  updateAITalkState,
  updateAIThinkState,
  setHistoryMsg,
  clearHistoryMsg,
  clearCurrentMsg,
  setInterruptMsg,
  attachMediaToLatestAIMsg,
  updateNetworkQuality,
  updateScene,
  updateSceneConfig,
  updateRTCConfig,
  updateShowSubtitle,
  updateFullScreen,
  updatecustomSceneName,
} = roomSlice.actions;

export default roomSlice.reducer;

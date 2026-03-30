import { Queue } from "bullmq";
import { QueueName, TQueueJobTypes } from "../queues";
import {
  createNewRedisInstance,
  redisQueueRetryOptions,
  getQueuePrefix,
} from "./redis";
import { logger } from "../logger";

export class AutoErrorAnalysisQueue {
  private static instance: Queue<
    TQueueJobTypes[QueueName.AutoErrorAnalysisQueue]
  > | null = null;

  public static getInstance(): Queue<
    TQueueJobTypes[QueueName.AutoErrorAnalysisQueue]
  > | null {
    if (AutoErrorAnalysisQueue.instance) return AutoErrorAnalysisQueue.instance;

    const newRedis = createNewRedisInstance({
      enableOfflineQueue: false,
      ...redisQueueRetryOptions,
    });

    AutoErrorAnalysisQueue.instance = newRedis
      ? new Queue<TQueueJobTypes[QueueName.AutoErrorAnalysisQueue]>(
          QueueName.AutoErrorAnalysisQueue,
          {
            connection: newRedis,
            prefix: getQueuePrefix(QueueName.AutoErrorAnalysisQueue),
            defaultJobOptions: {
              removeOnComplete: 1_000,
              removeOnFail: 10_000,
              attempts: 5,
              backoff: {
                type: "exponential",
                delay: 5000,
              },
            },
          },
        )
      : null;

    AutoErrorAnalysisQueue.instance?.on("error", (err) => {
      logger.error("AutoErrorAnalysisQueue error", err);
    });

    return AutoErrorAnalysisQueue.instance;
  }
}

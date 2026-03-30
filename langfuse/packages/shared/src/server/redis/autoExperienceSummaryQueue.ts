import { Queue } from "bullmq";
import { QueueName, TQueueJobTypes } from "../queues";
import {
  createNewRedisInstance,
  redisQueueRetryOptions,
  getQueuePrefix,
} from "./redis";
import { logger } from "../logger";

export class AutoExperienceSummaryQueue {
  private static instance: Queue<
    TQueueJobTypes[QueueName.AutoExperienceSummaryQueue]
  > | null = null;

  public static getInstance(): Queue<
    TQueueJobTypes[QueueName.AutoExperienceSummaryQueue]
  > | null {
    if (AutoExperienceSummaryQueue.instance)
      return AutoExperienceSummaryQueue.instance;

    const newRedis = createNewRedisInstance({
      enableOfflineQueue: false,
      ...redisQueueRetryOptions,
    });

    AutoExperienceSummaryQueue.instance = newRedis
      ? new Queue<TQueueJobTypes[QueueName.AutoExperienceSummaryQueue]>(
          QueueName.AutoExperienceSummaryQueue,
          {
            connection: newRedis,
            prefix: getQueuePrefix(QueueName.AutoExperienceSummaryQueue),
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

    AutoExperienceSummaryQueue.instance?.on("error", (err) => {
      logger.error("AutoExperienceSummaryQueue error", err);
    });

    return AutoExperienceSummaryQueue.instance;
  }
}

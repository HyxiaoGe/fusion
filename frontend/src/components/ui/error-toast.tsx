'use client';

import { useAppDispatch, useAppSelector } from '@/redux/hooks';
import { setGlobalError } from '@/redux/slices/conversationSlice';
import { useEffect } from 'react';
import { useToast } from './toast';

const ErrorToastContainer: React.FC = () => {
  const dispatch = useAppDispatch();
  const error = useAppSelector((state) => state.conversation.globalError);
  const { toast } = useToast();

  useEffect(() => {
    if (error) {
      toast({
        message: error,
        type: 'error',
        duration: 5000
      });
      
      // 清除错误，防止重复显示
      setTimeout(() => {
        dispatch(setGlobalError(null));
      }, 100);
    }
  }, [error, toast, dispatch]);

  return null;
};

export default ErrorToastContainer;
